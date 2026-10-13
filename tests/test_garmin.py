"""Garmin activity files (GPX, TCX) and Garmin's full data export."""

import io
import json
import zipfile

import pytest
from conftest import count

from jim.importers import garmin_export, garmin_files
from jim.models import decode_route, has_gps

GPX = """<?xml version="1.0"?>
<gpx creator="Garmin Connect" xmlns="http://www.topografix.com/GPX/1/1"
     xmlns:ns3="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
 <metadata><time>2026-10-04T11:51:00.000Z</time></metadata>
 <trk><name>Oakville Running</name><type>running</type><trkseg>
  <trkpt lat="43.4500" lon="-79.6800"><ele>90.0</ele><time>2026-10-04T11:51:00.000Z</time>
   <extensions><ns3:TrackPointExtension><ns3:hr>120</ns3:hr></ns3:TrackPointExtension></extensions></trkpt>
  <trkpt lat="43.4545" lon="-79.6800"><ele>95.0</ele><time>2026-10-04T11:54:00.000Z</time>
   <extensions><ns3:TrackPointExtension><ns3:hr>150</ns3:hr></ns3:TrackPointExtension></extensions></trkpt>
  <trkpt lat="43.4590" lon="-79.6800"><ele>101.0</ele><time>2026-10-04T11:57:00.000Z</time>
   <extensions><ns3:TrackPointExtension><ns3:hr>160</ns3:hr></ns3:TrackPointExtension></extensions></trkpt>
 </trkseg></trk>
</gpx>""".encode()

TCX = """<?xml version="1.0"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
 <Activities><Activity Sport="Running"><Id>2026-10-04T11:51:00.000Z</Id>
  <Lap StartTime="2026-10-04T11:51:00.000Z"><TotalTimeSeconds>360.0</TotalTimeSeconds>
   <DistanceMeters>1000.0</DistanceMeters><Calories>80</Calories>
   <AverageHeartRateBpm><Value>143</Value></AverageHeartRateBpm>
   <Track>
    <Trackpoint><Time>2026-10-04T11:51:00.000Z</Time>
     <Position><LatitudeDegrees>43.4500</LatitudeDegrees><LongitudeDegrees>-79.6800</LongitudeDegrees></Position>
     <AltitudeMeters>90.0</AltitudeMeters><HeartRateBpm><Value>120</Value></HeartRateBpm></Trackpoint>
    <Trackpoint><Time>2026-10-04T11:57:00.000Z</Time>
     <Position><LatitudeDegrees>43.4590</LatitudeDegrees><LongitudeDegrees>-79.6800</LongitudeDegrees></Position>
     <AltitudeMeters>101.0</AltitudeMeters><HeartRateBpm><Value>160</Value></HeartRateBpm></Trackpoint>
   </Track></Lap>
 </Activity></Activities>
</TrainingCenterDatabase>""".encode()

START = 1_791_114_660      # 2026-10-04 11:51:00 UTC


def test_gpx_gives_the_route_heart_rate_and_distance():
    workout = garmin_files.parse(GPX)
    assert (workout.title, workout.kind, workout.start_ts, workout.duration) == ("Oakville Running", "run", START, 360)
    assert len(workout.route) == 3 and has_gps(workout.route)
    assert workout.distance == pytest.approx(1000, rel=0.01)      # 0.009 degrees of latitude is 1 km
    assert workout.avg_hr == pytest.approx(143.3, abs=0.1)
    assert workout.elevation_gain == 11


def test_tcx_uses_the_lap_totals():
    workout = garmin_files.parse(TCX)
    assert (workout.kind, workout.start_ts, workout.duration, workout.distance, workout.calories) == (
        "run", START, 360, 1000, 80)
    assert has_gps(workout.route)


def test_activity_ids_come_from_garmin_file_names():
    assert garmin_files.activity_id_from_filename("activity_22777292791.tcx") == 22777292791
    assert garmin_files.activity_id_from_filename("22777292791.zip") == 22777292791
    assert garmin_files.activity_id_from_filename("22777292791_ACTIVITY.fit") == 22777292791
    assert garmin_files.activity_id_from_filename("my_run.gpx") is None
    assert garmin_files.activity_id_from_filename("401234567.fit") is None     # an upload ID, not an activity


def test_gpx_then_tcx_of_one_activity_is_one_workout(conn):
    garmin_files.import_file(conn, GPX, "activity_555.gpx")
    result = garmin_files.import_file(conn, TCX, "activity_555.tcx")
    assert (count(conn, "workouts"), count(conn, "workout_sources")) == (1, 1)
    assert not result.matched_existing      # the same record, refreshed; not a second source
    record = conn.execute("SELECT * FROM workout_sources").fetchone()
    assert record["external_id"] == "garmin-555"
    assert record["calories"] == 80                       # the TCX added calories...
    assert len(decode_route(record["route"])) == 3       # ...and the more detailed GPX track was kept


def test_a_file_without_an_id_still_matches_by_start_time(conn):
    garmin_files.import_file(conn, GPX)                        # no usable name
    garmin_files.import_file(conn, TCX, "activity_555.tcx")
    assert count(conn, "workout_sources") == 1
    assert conn.execute("SELECT external_id FROM workout_sources").fetchone()[0] == "garmin-555"


def test_rejects_files_that_are_not_activities():
    with pytest.raises(garmin_files.NotAnActivityFile):
        garmin_files.parse(b"<html><body>Sign in</body></html>")


# --- The full export ----------------------------------------------------------------------------

def activity(activity_id: int, start: int, name="Brampton Running", kind="running", **values) -> dict:
    return {"activityId": activity_id, "name": name, "activityType": kind, "beginTimestamp": start * 1000,
            "duration": 1_800_000, "distance": 500_000, "calories": 1673.6, "avgHr": 150, "elevationGain": 1200} | values


def export(*activities: dict) -> zipfile.ZipFile:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("DI_CONNECT/DI-Connect-Fitness/user_summarizedActivities.json",
                         json.dumps([{"summarizedActivitiesExport": list(activities)}]))
    return zipfile.ZipFile(data)


def test_export_units_are_converted(conn):
    result = garmin_export.import_archive(conn, export(activity(1, START)))
    assert result.activities == 1
    workout = conn.execute("SELECT * FROM workouts").fetchone()
    # Milliseconds, centimeters and kilojoules in; seconds, meters and kcal out.
    assert (workout["duration"], workout["distance"], workout["elevation_gain"]) == (1800, 5000, 12)
    assert workout["calories"] == pytest.approx(400)


def test_importing_the_export_again_adds_nothing(conn):
    garmin_export.import_archive(conn, export(activity(1, START), activity(2, START + 86_400)))
    again = garmin_export.import_archive(conn, export(activity(1, START), activity(2, START + 86_400)))
    assert (again.ingest.inserted, again.ingest.updated, again.removed) == (0, 2, 0)
    assert count(conn, "workouts") == 2


def test_an_activity_deleted_in_garmin_is_removed_but_newer_ones_are_kept(conn):
    days = [START + n * 86_400 for n in range(4)]
    garmin_export.import_archive(conn, export(activity(1, days[0]), activity(2, days[1]), activity(3, days[2])))
    garmin_files.import_file(conn, GPX.replace(b"2026-10-04", b"2026-10-09"), "activity_9.gpx")    # after the export
    result = garmin_export.import_archive(conn, export(activity(1, days[0]), activity(3, days[2])))
    assert result.removed == 1
    assert sorted(row[0] for row in conn.execute("SELECT external_id FROM workout_sources")) == [
        "garmin-1", "garmin-3", "garmin-9"]


def test_editing_the_start_time_in_garmin_does_not_duplicate(conn):
    garmin_export.import_archive(conn, export(activity(1, START)))
    garmin_export.import_archive(conn, export(activity(1, START + 7200)))       # moved two hours later
    assert count(conn, "workouts") == 1
    assert conn.execute("SELECT start_ts FROM workouts").fetchone()[0] == START + 7200


def test_a_file_imported_first_is_matched_by_the_export(conn):
    garmin_files.import_file(conn, GPX)        # keyed by start time: the file name had no ID
    garmin_export.import_archive(conn, export(activity(42, START, name="Oakville Running", duration=360_000,
                                                       distance=100_000)))
    assert (count(conn, "workouts"), count(conn, "workout_sources")) == (1, 1)
    record = conn.execute("SELECT * FROM workout_sources").fetchone()
    assert record["external_id"] == "garmin-42"
    assert has_gps(decode_route(record["route"]))      # the GPS track from the file is still there


def test_rejects_other_zips(conn):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("notes.txt", "hello")
    with pytest.raises(garmin_export.NotGarminExport):
        garmin_export.import_archive(conn, zipfile.ZipFile(data))

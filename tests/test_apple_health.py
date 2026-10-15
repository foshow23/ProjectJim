"""Apple Health's export.xml: body measurements, and heart rate and calories filled in on workouts from
Hevy and Garmin (never workouts of its own, and never food)."""

import io
import zipfile
from datetime import datetime

import pytest
from conftest import count

from jim import merger
from jim.importers import apple_health
from jim.models import GARMIN, HEVY, SourceWorkout

Q = "HKQuantityTypeIdentifier"


def record(kind: str, source: str, start: str, value, unit: str, end: str | None = None) -> str:
    return (f'<Record type="{Q}{kind}" sourceName="{source}" unit="{unit}" startDate="{start} -0400" '
            f'endDate="{end or start} -0400" value="{value}"/>')


def sleep(start: str, end: str, stage: str) -> str:
    return (f'<Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Garmin Connect" '
            f'startDate="{start} -0400" endDate="{end} -0400" value="HKCategoryValueSleepAnalysis{stage}"/>')


def export(*elements: str) -> io.BytesIO:
    return io.BytesIO(('<?xml version="1.0"?><HealthData locale="en_CA">' + "".join(elements) + "</HealthData>").encode())


def timestamp(text: str) -> float:
    return datetime.strptime(text + " -0400", "%Y-%m-%d %H:%M:%S %z").timestamp()


RUN = (f'<Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="30" durationUnit="min" '
       f'sourceName="Garmin Connect" startDate="2026-10-10 07:00:00 -0400" endDate="2026-10-10 07:30:00 -0400">'
       f'<WorkoutStatistics type="{Q}DistanceWalkingRunning" sum="5.05" unit="km"/>'
       f'<WorkoutStatistics type="{Q}ActiveEnergyBurned" sum="400" unit="Cal"/>'
       f'<WorkoutStatistics type="{Q}HeartRate" average="158" unit="count/min"/></Workout>')
LIFT = ('<Workout workoutActivityType="HKWorkoutActivityTypeTraditionalStrengthTraining" duration="48" '
        'durationUnit="min" sourceName="Hevy" startDate="2026-10-06 06:43:00 -0400" '
        f'endDate="2026-10-06 07:31:00 -0400"><WorkoutStatistics type="{Q}HeartRate" average="110" unit="count/min"/>'
        f'<WorkoutStatistics type="{Q}ActiveEnergyBurned" sum="250" unit="Cal"/></Workout>')


def add_garmin_run_and_hevy_lift(conn):
    """What the Garmin and Hevy exports bring in: names and sets, but no calories on the run and
    no heart rate or calories on the lift."""
    merger.ingest(conn, [
        SourceWorkout(source=GARMIN, external_id="garmin-77", app_name="Garmin Connect", kind="run",
                      title="Brampton Running", start_ts=timestamp("2026-10-10 07:00:02"), duration=1795.0,
                      distance=5000.0, avg_hr=157.0),
        SourceWorkout(source=HEVY, external_id="h1", app_name="Hevy", kind="strength", title="Push Day",
                      start_ts=timestamp("2026-10-06 06:43:00"), duration=2880.0),
    ])


def test_health_fills_in_heart_rate_and_calories(conn):
    add_garmin_run_and_hevy_lift(conn)
    result = apple_health.import_export(conn, export(RUN, LIFT))
    assert (result.workouts_read, result.workouts_filled_in) == (2, 2)
    assert count(conn, "workouts") == 2         # nothing new was created
    lift = conn.execute("SELECT * FROM workouts WHERE kind='strength'").fetchone()
    assert (lift["title"], lift["avg_hr"], lift["calories"]) == ("Push Day", 110.0, 250.0)
    run = conn.execute("SELECT * FROM workouts WHERE kind='run'").fetchone()
    # Still the Garmin file's name and numbers; Health only filled in what was missing (calories).
    assert (run["title"], run["distance"], run["avg_hr"], run["calories"]) == ("Brampton Running", 5000.0, 157.0, 400.0)


def test_a_health_workout_with_nothing_to_fill_in_is_skipped(conn):
    # No Hevy or Garmin workouts yet, so these would only show up as a bare "Run" and "Strength".
    result = apple_health.import_export(conn, export(RUN, LIFT))
    assert (result.workouts_read, result.workouts_filled_in) == (2, 0)
    assert (count(conn, "workouts"), count(conn, "workout_sources")) == (0, 0)


def test_health_only_workouts_from_older_imports_are_removed(conn):
    workout_id = conn.execute("INSERT INTO workouts (kind, title, start_ts, duration) VALUES ('strength', 'Strength', ?, 2880)",
                              (timestamp("2026-10-14 19:54:00"),)).lastrowid
    conn.execute("INSERT INTO workout_sources (workout_id, source, external_id, app_name, kind, start_ts, duration,"
                 " updated_at) VALUES (?, 'apple_health', 'Hevy|1', 'Hevy', 'strength', ?, 2880, 0)",
                 (workout_id, timestamp("2026-10-14 19:54:00")))
    apple_health.import_export(conn, export())
    assert (count(conn, "workouts"), count(conn, "workout_sources")) == (0, 0)


def test_a_workout_deleted_in_hevy_takes_its_health_copy_with_it(conn):
    add_garmin_run_and_hevy_lift(conn)
    apple_health.import_export(conn, export(LIFT))
    merger.remove_sources(conn, HEVY, lambda record: True)      # as if deleted in Hevy
    assert [row["kind"] for row in conn.execute("SELECT kind FROM workouts")] == ["run"]


def test_food_in_the_export_is_ignored(conn):
    apple_health.import_export(conn, export(record("DietaryEnergyConsumed", "Lose It!", "2026-10-10 12:00:00", 2100, "Cal")))
    assert count(conn, "food_entries") == 0


def test_importing_the_export_again_changes_nothing(conn):
    add_garmin_run_and_hevy_lift(conn)
    apple_health.import_export(conn, export(RUN, LIFT, record("BodyMass", "Scale", "2026-10-10 07:10:00", 82, "kg")))
    apple_health.import_export(conn, export(RUN, LIFT, record("BodyMass", "Scale", "2026-10-10 07:10:00", 82, "kg")))
    assert (count(conn, "workouts"), count(conn, "workout_sources"), count(conn, "body_metrics")) == (2, 4, 1)


def test_steps_count_one_device_per_day(conn):
    apple_health.import_export(conn, export(
        record("StepCount", "Garmin Connect", "2026-10-10 09:00:00", 4000, "count"),
        record("StepCount", "Garmin Connect", "2026-10-10 15:00:00", 5000, "count"),
        record("StepCount", "iPhone", "2026-10-10 09:00:00", 2500, "count"),       # the same steps, seen by the phone
    ))
    rows = conn.execute("SELECT value, source FROM body_metrics WHERE metric='steps'").fetchall()
    assert [(row["value"], row["source"]) for row in rows] == [(9000.0, "Garmin Connect")]


def test_measurements_are_converted_and_duplicates_dropped(conn):
    apple_health.import_export(conn, export(
        record("BodyMass", "Scale", "2026-10-10 07:10:00", 180, "lb"),
        record("BodyMass", "Garmin Connect", "2026-10-10 07:10:20", 180, "lb"),    # the same weigh-in, synced on
        record("BodyFatPercentage", "Scale", "2026-10-10 07:10:00", 0.185, "%"),
        record("RestingHeartRate", "Garmin Connect", "2026-10-10 06:00:00", 52, "count/min"),
        record("RestingHeartRate", "Garmin Connect", "2026-10-10 20:00:00", 56, "count/min"),
    ))
    values = {row["metric"]: row["value"] for row in conn.execute("SELECT metric, value FROM body_metrics")}
    assert values["weight"] == pytest.approx(81.647, abs=0.001)
    assert values["body_fat"] == 0.185
    assert values["resting_hr"] == 54       # the day's average
    assert conn.execute("SELECT COUNT(*) FROM body_metrics WHERE metric='weight'").fetchone()[0] == 1


def test_sleep_adds_up_stages_without_double_counting_overlaps(conn):
    assert apple_health.sleep_hours([(0, 3600), (1800, 5400), (7200, 9000)]) == 2.0
    apple_health.import_export(conn, export(
        sleep("2026-10-09 23:00:00", "2026-10-10 07:00:00", "InBed"),       # in bed isn't asleep
        sleep("2026-10-09 23:30:00", "2026-10-10 02:30:00", "AsleepCore"),
        sleep("2026-10-10 02:30:00", "2026-10-10 03:30:00", "AsleepDeep"),
        sleep("2026-10-10 04:00:00", "2026-10-10 06:30:00", "AsleepREM"),
    ))
    assert conn.execute("SELECT value FROM body_metrics WHERE metric='sleep_hours'").fetchone()[0] == 6.5


def test_rejects_files_that_are_not_a_health_export(conn):
    with pytest.raises(apple_health.NotAppleHealthExport):
        apple_health.import_export(conn, io.BytesIO(b"<gpx></gpx>"))
    archive = zipfile.ZipFile(io.BytesIO(), "w")
    assert not apple_health.is_apple_health_export(archive)

"""Apple Health's export.xml, including not doubling what Hevy and Garmin already brought in."""

import io
import zipfile
from datetime import datetime

import pytest
from conftest import count

from jim import merger, nutrition
from jim.importers import apple_health, hevy
from jim.models import GARMIN, SourceWorkout

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
        'endDate="2026-10-06 07:31:00 -0400"/>')


def test_health_workouts_join_the_hevy_and_garmin_ones_instead_of_duplicating(conn):
    # Already here: a Garmin file of the run, and the Hevy export of the lift.
    merger.ingest(conn, [SourceWorkout(source=GARMIN, external_id="garmin-77", app_name="Garmin Connect", kind="run",
                                       title="Brampton Running", start_ts=timestamp("2026-10-10 07:00:02"),
                                       duration=1795.0, distance=5000.0, avg_hr=157.0)])
    conn.execute("INSERT INTO workouts (kind, title, start_ts, duration) VALUES ('strength', 'Push Day', ?, 2880)",
                 (timestamp("2026-10-06 06:43:00"),))
    lift_id = conn.execute("SELECT id FROM workouts WHERE kind='strength'").fetchone()[0]
    conn.execute("INSERT INTO workout_sources (workout_id, source, external_id, app_name, kind, title, start_ts,"
                 " duration, updated_at) VALUES (?, 'hevy', 'h1', 'Hevy', 'strength', 'Push Day', ?, 2880, 0)",
                 (lift_id, timestamp("2026-10-06 06:43:00")))

    result = apple_health.import_export(conn, export(RUN, LIFT))

    assert result.workouts == 2
    assert result.ingest.merged_into_existing == 2      # both matched; nothing new was created
    assert count(conn, "workouts") == 2
    run = conn.execute("SELECT * FROM workouts WHERE kind='run'").fetchone()
    # Still the Garmin file's name and numbers; Health only filled in what was missing (calories).
    assert (run["title"], run["distance"], run["avg_hr"], run["calories"]) == ("Brampton Running", 5000.0, 157.0, 400.0)
    sources = [row["source"] for row in conn.execute("SELECT source FROM workout_sources WHERE workout_id=?", (run["id"],))]
    assert sorted(sources) == ["apple_health", "garmin"]


def test_importing_the_export_again_changes_nothing(conn):
    apple_health.import_export(conn, export(RUN, LIFT, record("BodyMass", "Scale", "2026-10-10 07:10:00", 82, "kg")))
    again = apple_health.import_export(conn, export(RUN, LIFT, record("BodyMass", "Scale", "2026-10-10 07:10:00", 82, "kg")))
    assert (again.ingest.inserted, again.ingest.updated) == (0, 2)
    assert (count(conn, "workouts"), count(conn, "body_metrics")) == (2, 1)


def test_a_workout_deleted_from_health_is_removed_on_the_next_import(conn):
    apple_health.import_export(conn, export(RUN, LIFT))
    result = apple_health.import_export(conn, export(RUN))
    assert result.removed == 1
    assert [row["kind"] for row in conn.execute("SELECT kind FROM workouts")] == ["run"]


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


def test_a_food_log_takes_priority_over_health_totals(conn):
    apple_health.import_export(conn, export(
        record("DietaryEnergyConsumed", "Lose It!", "2026-10-10 12:00:00", 2100, "Cal"),
        record("DietaryProtein", "Lose It!", "2026-10-10 12:00:00", 140, "g"),
        record("DietaryEnergyConsumed", "Lose It!", "2026-10-11 12:00:00", 1900, "Cal"),
        record("DietaryWater", "WaterMinder", "2026-10-10 12:00:00", 2500, "mL"),
    ))
    # The same Oct 10 food also arrives as individual entries (a Lose It! export).
    conn.execute("INSERT INTO food_entries (day, meal, name, quantity, units, calories, protein, source)"
                 " VALUES ('2026-10-10', 'Lunch', 'Jollof Rice', 1, 'Cups', 2100, 140, 'Lose It!')")
    logged, health_only = nutrition.days(conn, datetime(2026, 10, 11).date(), 2)
    assert (logged.calories, logged.protein, logged.water) == (2100, 140, 2.5)     # counted once, water kept
    assert logged.source == "Lose It! food log"
    assert (health_only.calories, health_only.source) == (1900, "Lose It! via Apple Health")


def test_rejects_files_that_are_not_a_health_export(conn):
    with pytest.raises(apple_health.NotAppleHealthExport):
        apple_health.import_export(conn, io.BytesIO(b"<gpx></gpx>"))
    archive = zipfile.ZipFile(io.BytesIO(), "w")
    assert not apple_health.is_apple_health_export(archive)

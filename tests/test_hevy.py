"""Hevy's CSV export."""

import pytest
from conftest import count

from jim import db, merger
from jim.importers import hevy
from jim.models import GARMIN, SourceWorkout

HEADER = ("title,start_time,end_time,description,exercise_title,superset_id,exercise_notes,set_index,set_type,"
          "weight_lbs,reps,distance_miles,duration_seconds,rpe\n")
PUSH = ('"Push Day","6 Oct 2026, 06:43","6 Oct 2026, 07:31","","Bench Press (Barbell)",,"",0,"warmup",45,10,,,\n'
        '"Push Day","6 Oct 2026, 06:43","6 Oct 2026, 07:31","","Bench Press (Barbell)",,"",1,"normal",135,8,,,8.5\n'
        '"Push Day","6 Oct 2026, 06:43","6 Oct 2026, 07:31","","Plank",,"",0,"normal",,,,60,\n')
LEGS = '"Leg Day","8 Oct 2026, 18:00","8 Oct 2026, 19:05","","Squat (Barbell)",,"",0,"normal",225,5,,,\n'


def test_parses_workouts_sets_and_units():
    workouts, uses_pounds = hevy.parse(HEADER + PUSH + LEGS)
    assert uses_pounds
    assert [w.title for w in workouts] == ["Push Day", "Leg Day"]
    push = workouts[0]
    assert push.duration == 48 * 60
    assert [(s.exercise_index, s.exercise_title, s.set_type) for s in push.sets] == [
        (0, "Bench Press (Barbell)", "warmup"), (0, "Bench Press (Barbell)", "normal"), (1, "Plank", "normal")]
    assert push.sets[1].weight_kg == pytest.approx(61.235, abs=0.001)    # 135 lb
    assert (push.sets[1].reps, push.sets[1].rpe, push.sets[2].duration_s) == (8, 8.5, 60)


def test_importing_again_adds_nothing(conn):
    first = hevy.import_csv(conn, HEADER + PUSH + LEGS)
    again = hevy.import_csv(conn, HEADER + PUSH + LEGS)
    assert (first.workouts, first.sets, first.ingest.inserted) == (2, 4, 2)
    assert (again.ingest.inserted, again.ingest.updated, again.removed) == (0, 2, 0)
    assert (count(conn, "workouts"), count(conn, "exercise_sets")) == (2, 4)
    assert db.get_setting(conn, "weight_unit") == "lb"


def test_a_workout_deleted_in_hevy_is_removed_but_older_ones_are_kept(conn):
    older = '"Old","1 Sep 2026, 08:00","1 Sep 2026, 09:00","","Curl",,"",0,"normal",30,10,,,\n'
    middle = '"Middle","7 Oct 2026, 08:00","7 Oct 2026, 09:00","","Curl",,"",0,"normal",30,10,,,\n'
    hevy.import_csv(conn, HEADER + older + PUSH + middle + LEGS)
    # A newer file that starts later (older workout simply isn't in it) and no longer has "Middle".
    result = hevy.import_csv(conn, HEADER + PUSH + LEGS)
    assert result.removed == 1
    assert [row["title"] for row in conn.execute("SELECT title FROM workouts ORDER BY start_ts")] == [
        "Old", "Push Day", "Leg Day"]


def test_merges_with_the_watch_recording_of_the_same_session(conn):
    start = hevy.parse_date("6 Oct 2026, 06:43")
    merger.ingest(conn, [SourceWorkout(source=GARMIN, external_id="garmin-5", app_name="Garmin Connect",
                                       kind="strength", title="Strength", start_ts=start + 20, duration=2800.0,
                                       avg_hr=121.0)])
    result = hevy.import_csv(conn, HEADER + PUSH)
    assert result.ingest.merged_into_existing == 1
    workout = conn.execute("SELECT * FROM workouts").fetchone()
    assert (workout["title"], workout["avg_hr"]) == ("Push Day", 121.0)


def test_rejects_other_csv_files(conn):
    with pytest.raises(hevy.NotHevyExport):
        hevy.import_csv(conn, "Date,Name,Calories\n10/01/2026,Toast,80\n")

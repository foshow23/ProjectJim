"""One workout per real session, however many sources recorded it."""

from conftest import START, count, garmin, hevy

from jim import merger
from jim.models import APPLE_HEALTH, SourceWorkout


def health(app="Garmin Connect", start=START, duration=3000.0, kind="strength", **values) -> SourceWorkout:
    return SourceWorkout(source=APPLE_HEALTH, external_id=f"{app}|{int(start)}", app_name=app, kind=kind, title=None,
                         start_ts=start, duration=duration, **values)


def test_importing_the_same_record_twice_updates_it(conn):
    first = merger.ingest(conn, [garmin(distance=5000.0)])
    second = merger.ingest(conn, [garmin(distance=5100.0)])
    assert (first.inserted, second.inserted, second.updated) == (1, 0, 1)
    assert count(conn, "workouts") == 1
    assert conn.execute("SELECT distance FROM workouts").fetchone()[0] == 5100.0


def test_hevy_and_the_watch_become_one_workout(conn):
    merger.ingest(conn, [hevy()])
    result = merger.ingest(conn, [garmin(kind="strength", start=START + 60, duration=2900.0, title="Strength",
                                         avg_hr=128.0, calories=310.0)])
    assert result.merged_into_existing == 1
    assert count(conn, "workouts") == 1
    workout = conn.execute("SELECT * FROM workouts").fetchone()
    # The name comes from Hevy; timing, heart rate and calories from the watch.
    assert workout["title"] == "Upper Body"
    assert (workout["start_ts"], workout["avg_hr"], workout["calories"]) == (START + 60, 128.0, 310.0)
    assert count(conn, "exercise_sets") == 1


def test_two_recordings_from_the_same_app_stay_separate(conn):
    merger.ingest(conn, [garmin("garmin-1", duration=1800.0), garmin("garmin-2", start=START + 600, duration=1800.0)])
    assert count(conn, "workouts") == 2


def test_different_kinds_or_distances_do_not_merge(conn):
    merger.ingest(conn, [garmin(distance=5000.0)])
    merger.ingest(conn, [health(app="Watch", kind="ride", duration=1800.0)])                    # a ride isn't a run
    merger.ingest(conn, [health(app="Phone", kind="run", duration=1800.0, distance=8000.0)])    # 60% further
    assert count(conn, "workouts") == 3


def test_a_small_overlap_is_not_the_same_session(conn):
    merger.ingest(conn, [hevy(duration=1800.0)])
    merger.ingest(conn, [health(start=START + 1500, duration=1800.0)])      # only the last 5 of 30 minutes
    assert count(conn, "workouts") == 2


def test_hike_and_walk_count_as_the_same_kind(conn):
    merger.ingest(conn, [garmin(kind="hike", title="Trail")])
    merger.ingest(conn, [health(kind="walk", duration=1800.0)])
    assert count(conn, "workouts") == 1
    assert conn.execute("SELECT kind FROM workouts").fetchone()[0] == "hike"


def test_removing_a_source_keeps_the_rest_and_deletes_empty_workouts(conn):
    merger.ingest(conn, [hevy(), garmin("garmin-9", start=START + 90_000)])
    merger.ingest(conn, [health(avg_hr=120.0)])
    assert count(conn, "workouts") == 2

    removed = merger.remove_sources(conn, "garmin", lambda record: True)
    assert removed == 1
    assert count(conn, "workouts") == 1     # the run had no other source; the lift still has two
    assert count(conn, "workout_sources") == 2


def test_consolidate_joins_workouts_ingested_apart(conn):
    # Each is the first record of its session when it arrives, so they start as two workouts.
    conn.execute("INSERT INTO workouts (id, kind, title, start_ts, duration) VALUES (1, 'strength', 'A', ?, 3000)", (START,))
    conn.execute("INSERT INTO workouts (id, kind, title, start_ts, duration) VALUES (2, 'strength', 'B', ?, 3000)", (START + 30,))
    for workout_id, source, app in [(1, "hevy", "Hevy"), (2, "garmin", "Garmin Connect")]:
        conn.execute("INSERT INTO workout_sources (workout_id, source, external_id, app_name, kind, title, start_ts,"
                     " duration, updated_at) VALUES (?, ?, 'x', ?, 'strength', ?, ?, 3000, 0)",
                     (workout_id, source, app, app, START))
    assert merger.consolidate(conn) == 1
    assert count(conn, "workouts") == 1
    assert merger.consolidate(conn) == 0

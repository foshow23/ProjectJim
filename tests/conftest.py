"""Shared test setup. Every test uses made-up data and its own empty in-memory database."""

import pytest

from jim import db
from jim.models import GARMIN, HEVY, SourceSet, SourceWorkout


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    yield connection
    connection.close()


START = 1_780_000_000.0     # an arbitrary moment; tests only care about offsets from it


def garmin(key="garmin-1", start=START, duration=1800.0, kind="run", **values) -> SourceWorkout:
    return SourceWorkout(source=GARMIN, external_id=key, app_name="Garmin Connect", kind=kind,
                         title=values.pop("title", "Morning Run"), start_ts=start, duration=duration, **values)


def hevy(start=START, duration=3000.0, title="Upper Body", sets=None) -> SourceWorkout:
    sets = sets if sets is not None else [SourceSet(0, "Bench Press", 0, weight_kg=60.0, reps=8)]
    return SourceWorkout(source=HEVY, external_id=str(int(start)), app_name="Hevy", kind="strength", title=title,
                         start_ts=start, duration=duration, sets=sets)


def count(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

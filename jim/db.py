"""SQLite storage. Everything ProjectJim knows lives in one file: data/projectjim.db."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "projectjim.db"

SCHEMA = """
-- One row per real workout, merged from every source that recorded it.
CREATE TABLE IF NOT EXISTS workouts (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,             -- run, walk, hike, ride, swim, strength, other
    title TEXT NOT NULL,
    start_ts REAL NOT NULL,         -- seconds since 1970 (UTC)
    duration REAL NOT NULL,         -- seconds
    distance REAL,                  -- meters
    calories REAL,                  -- active kcal
    avg_hr REAL,
    elevation_gain REAL             -- meters
);
CREATE INDEX IF NOT EXISTS workouts_start ON workouts(start_ts);

-- What each source said about a workout, kept as-is so merged values can be recomputed.
CREATE TABLE IF NOT EXISTS workout_sources (
    id INTEGER PRIMARY KEY,
    workout_id INTEGER REFERENCES workouts(id),
    source TEXT NOT NULL,           -- apple_health, hevy, garmin
    external_id TEXT NOT NULL,      -- the source's own ID for it
    app_name TEXT NOT NULL,         -- e.g. "Garmin Connect"
    kind TEXT NOT NULL,
    title TEXT,
    start_ts REAL NOT NULL,
    duration REAL NOT NULL,
    distance REAL,
    calories REAL,
    avg_hr REAL,
    elevation_gain REAL,
    route BLOB,                     -- zlib-compressed JSON track, see jim.models
    updated_at REAL NOT NULL,
    UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS workout_sources_workout ON workout_sources(workout_id);

-- Sets logged in Hevy.
CREATE TABLE IF NOT EXISTS exercise_sets (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES workout_sources(id) ON DELETE CASCADE,
    exercise_index INTEGER NOT NULL,
    exercise_title TEXT NOT NULL,
    set_index INTEGER NOT NULL,
    set_type TEXT NOT NULL,         -- normal, warmup, failure, dropset
    weight_kg REAL,
    reps INTEGER,
    distance_m REAL,
    duration_s REAL,
    rpe REAL,
    superset_id INTEGER
);
CREATE INDEX IF NOT EXISTS exercise_sets_source ON exercise_sets(source_id);

-- One logged food. Nutrients are for the amount eaten.
CREATE TABLE IF NOT EXISTS food_entries (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,              -- YYYY-MM-DD
    meal TEXT NOT NULL,
    name TEXT NOT NULL,
    quantity REAL NOT NULL,
    units TEXT NOT NULL,
    calories REAL NOT NULL,
    protein REAL, carbs REAL, fat REAL, saturated_fat REAL, sugars REAL, fiber REAL,
    cholesterol REAL,               -- mg
    sodium REAL,                    -- mg
    source TEXT NOT NULL,           -- "Lose It!" or "ProjectJim"
    food_json TEXT,                 -- the database food, for re-logging (ProjectJim entries)
    grams REAL
);
CREATE INDEX IF NOT EXISTS food_entries_day ON food_entries(day);

-- Your food library: foods you created and custom foods imported from Lose It!.
CREATE TABLE IF NOT EXISTS saved_foods (
    food_id TEXT PRIMARY KEY,
    food_json TEXT NOT NULL,
    source TEXT NOT NULL,
    updated_at REAL NOT NULL
);

-- Earlier versions kept Apple Health's daily food totals; food now comes only from food logs.
DROP TABLE IF EXISTS daily_nutrition;

-- Body measurements: weight (kg), body_fat (0-1), resting_hr, hrv (ms), vo2max,
-- steps and active_energy (per day), sleep_hours (per night, by wake-up day).
CREATE TABLE IF NOT EXISTS body_metrics (
    metric TEXT NOT NULL,
    ts REAL NOT NULL,
    value REAL NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (metric, ts, source)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL             -- JSON
);
"""


def connect(path: str | os.PathLike | None = None) -> sqlite3.Connection:
    """Opens (and creates if needed) the database. Pass ":memory:" for tests."""
    path = path or os.environ.get("PROJECTJIM_DB") or DEFAULT_PATH
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # Streamlit handles each interaction on a different thread. There's one local user, so
    # sharing the connection across threads is fine.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def get_setting(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )
    conn.commit()

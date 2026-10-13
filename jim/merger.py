"""Consolidates workouts from every source into one row per real session.

Matching rules:
1. Same source and same ID -> the same record; it's updated in place.
2. Different sources -> the same session when the kinds are compatible, the time ranges overlap
   by at least half of the shorter one, and (when both have one) the distances are within 10%.
3. A workout never takes two records from the same app (e.g. two Garmin runs), so back-to-back
   sessions stay separate.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from .models import (APPLE_HEALTH, GARMIN, HEVY, KIND_NAMES, SourceWorkout, encode_route,
                     kinds_can_merge)

MINIMUM_OVERLAP = 0.5
MAXIMUM_DISTANCE_DIFFERENCE = 0.1
# Treat very short or zero-length records as lasting at least this long when comparing.
MINIMUM_DURATION = 60.0


@dataclass
class IngestResult:
    inserted: int = 0           # records seen for the first time
    updated: int = 0            # records that already existed and were refreshed
    created_workouts: int = 0   # new workouts; the rest of `inserted` joined an existing one

    @property
    def merged_into_existing(self) -> int:
        return self.inserted - self.created_workouts


def overlap_score(kind: str, start: float, duration: float, distance: float | None,
                  other_kind: str, other_start: float, other_duration: float,
                  other_distance: float | None) -> float | None:
    """How much two recordings overlap in time (0-1 of the shorter one), or None if they can't
    be the same session."""
    if not kinds_can_merge(kind, other_kind):
        return None
    length = max(duration, MINIMUM_DURATION)
    other_length = max(other_duration, MINIMUM_DURATION)
    overlap = min(start + length, other_start + other_length) - max(start, other_start)
    score = overlap / min(length, other_length)
    if score < MINIMUM_OVERLAP:
        return None
    if distance and other_distance:
        if abs(distance - other_distance) / max(distance, other_distance) > MAXIMUM_DISTANCE_DIFFERENCE:
            return None
    return score


@dataclass
class _Candidate:
    """The parts of a stored workout that matching looks at."""
    id: int
    kind: str
    start: float
    duration: float
    distance: float | None
    apps: set[str]      # "source|app" for every record in it, e.g. "apple_health|Garmin Connect"

    def score(self, item: SourceWorkout) -> float | None:
        if f"{item.source}|{item.app_name}" in self.apps:
            return None
        return overlap_score(item.kind, item.start_ts, item.duration, item.distance,
                             self.kind, self.start, self.duration, self.distance)


def _load_candidates(conn: sqlite3.Connection, start: float, end: float) -> list[_Candidate]:
    rows = conn.execute(
        "SELECT id, kind, start_ts, duration, distance FROM workouts WHERE start_ts BETWEEN ? AND ?",
        (start, end),
    ).fetchall()
    apps: dict[int, set[str]] = {}
    for row in conn.execute("SELECT workout_id, source, app_name FROM workout_sources WHERE workout_id IS NOT NULL"):
        apps.setdefault(row["workout_id"], set()).add(f"{row['source']}|{row['app_name']}")
    return [_Candidate(r["id"], r["kind"], r["start_ts"], r["duration"], r["distance"], apps.get(r["id"], set()))
            for r in rows]


def ingest(conn: sqlite3.Connection, items: list[SourceWorkout]) -> IngestResult:
    """Adds or refreshes records, attaching each new one to the workout it belongs to."""
    result = IngestResult()
    if not items:
        return result

    existing = {(r["source"], r["external_id"]): (r["id"], r["workout_id"])
                for r in conn.execute("SELECT id, source, external_id, workout_id FROM workout_sources")}
    # Only workouts near the incoming ones can match them.
    day = 24 * 60 * 60
    candidates = _load_candidates(conn, min(i.start_ts for i in items) - day, max(i.start_ts for i in items) + day)
    by_id = {c.id: c for c in candidates}

    for item in items:
        key = (item.source, item.external_id)
        if key in existing:
            source_id, workout_id = existing[key]
            _apply(conn, source_id, item)
            if workout_id is not None:
                _refresh(conn, workout_id, by_id)
            result.updated += 1
            continue

        scored = [(c.score(item), c) for c in candidates]
        scored = [(score, c) for score, c in scored if score is not None]
        if scored:
            workout_id = max(scored, key=lambda pair: pair[0])[1].id
        else:
            workout_id = conn.execute(
                "INSERT INTO workouts (kind, title, start_ts, duration) VALUES (?, ?, ?, ?)",
                (item.kind, item.title or KIND_NAMES[item.kind], item.start_ts, item.duration),
            ).lastrowid
            candidate = _Candidate(workout_id, item.kind, item.start_ts, item.duration, item.distance, set())
            candidates.append(candidate)
            by_id[workout_id] = candidate
            result.created_workouts += 1

        source_id = conn.execute(
            "INSERT INTO workout_sources (workout_id, source, external_id, app_name, kind, start_ts, duration, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (workout_id, item.source, item.external_id, item.app_name, item.kind, item.start_ts, item.duration,
             time.time()),
        ).lastrowid
        _apply(conn, source_id, item)
        existing[key] = (source_id, workout_id)
        result.inserted += 1
        # Refresh now so later items in this batch match against up-to-date values.
        _refresh(conn, workout_id, by_id)
        by_id[workout_id].apps.add(f"{item.source}|{item.app_name}")

    conn.commit()
    return result


def _apply(conn: sqlite3.Connection, source_id: int, item: SourceWorkout) -> None:
    """Writes a record's values. Routes and sets are only replaced when the source provides them."""
    conn.execute(
        "UPDATE workout_sources SET app_name=?, kind=?, title=?, start_ts=?, duration=?, distance=?, calories=?,"
        " avg_hr=?, elevation_gain=?, updated_at=? WHERE id=?",
        (item.app_name, item.kind, item.title, item.start_ts, item.duration, item.distance, item.calories,
         item.avg_hr, item.elevation_gain, time.time(), source_id),
    )
    if item.route is not None:
        conn.execute("UPDATE workout_sources SET route=? WHERE id=?",
                     (encode_route(item.route) if item.route else None, source_id))
    if item.sets is not None:
        conn.execute("DELETE FROM exercise_sets WHERE source_id=?", (source_id,))
        conn.executemany(
            "INSERT INTO exercise_sets (source_id, exercise_index, exercise_title, set_index, set_type, weight_kg,"
            " reps, distance_m, duration_s, rpe, superset_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(source_id, s.exercise_index, s.exercise_title, s.set_index, s.set_type, s.weight_kg, s.reps,
              s.distance_m, s.duration_s, s.rpe, s.superset_id) for s in item.sets],
        )


def _refresh(conn: sqlite3.Connection, workout_id: int, by_id: dict[int, _Candidate]) -> None:
    recompute(conn, workout_id)
    row = conn.execute("SELECT kind, start_ts, duration, distance FROM workouts WHERE id=?", (workout_id,)).fetchone()
    if row and workout_id in by_id:
        candidate = by_id[workout_id]
        candidate.kind, candidate.start, candidate.duration, candidate.distance = (
            row["kind"], row["start_ts"], row["duration"], row["distance"])


def _ranked(records: list[sqlite3.Row], order: list[str]) -> list[sqlite3.Row]:
    def key(record: sqlite3.Row):
        rank = order.index(record["source"]) if record["source"] in order else len(order)
        # Several apps can write the same session to Apple Health (e.g. Garmin and Hevy).
        # The one with heart rate came from a watch, so trust it first.
        return (rank, record["avg_hr"] is None, record["external_id"])
    return sorted(records, key=key)


def recompute(conn: sqlite3.Connection, workout_id: int) -> None:
    """Rebuilds a workout's values from its records, or deletes it if none are left.

    Device recordings win for timing and body metrics (a Garmin file is the watch's own data,
    then Apple Health); names come from where people actually name things (Hevy, Garmin).
    """
    records = conn.execute("SELECT * FROM workout_sources WHERE workout_id=?", (workout_id,)).fetchall()
    if not records:
        conn.execute("DELETE FROM workouts WHERE id=?", (workout_id,))
        return
    device = _ranked(records, [GARMIN, APPLE_HEALTH, HEVY])
    named = _ranked(records, [HEVY, GARMIN, APPLE_HEALTH])

    def first(rows, column, positive=True):
        return next((r[column] for r in rows if r[column] is not None and (r[column] > 0 or not positive)), None)

    kind = next((r["kind"] for r in device if r["kind"] != "other"), device[0]["kind"])
    title = next((r["title"] for r in named if r["title"]), None) or KIND_NAMES[kind]
    conn.execute(
        "UPDATE workouts SET kind=?, title=?, start_ts=?, duration=?, distance=?, calories=?, avg_hr=?,"
        " elevation_gain=? WHERE id=?",
        (kind, title, device[0]["start_ts"], first(device, "duration") or 0, first(device, "distance"),
         first(device, "calories"), first(device, "avg_hr"), first(device, "elevation_gain", positive=False),
         workout_id),
    )


def remove_sources(conn: sqlite3.Connection, source: str, should_remove) -> int:
    """Removes records of one source that `should_remove(row)` selects (e.g. deleted upstream).

    Workouts left with no records are deleted. Returns how many records were removed.
    """
    doomed = [r for r in conn.execute("SELECT * FROM workout_sources WHERE source=?", (source,)) if should_remove(r)]
    for record in doomed:
        conn.execute("DELETE FROM workout_sources WHERE id=?", (record["id"],))
    for workout_id in {r["workout_id"] for r in doomed if r["workout_id"] is not None}:
        recompute(conn, workout_id)
    conn.commit()
    return len(doomed)


def consolidate(conn: sqlite3.Connection) -> int:
    """Merges separate workouts that turn out to be the same session.

    Ingesting one record at a time can't always tell (the first record of a session has
    nothing to match yet). Safe to run after every import. Returns how many were merged.
    """
    workouts = _load_candidates(conn, float("-inf"), float("inf"))
    workouts.sort(key=lambda w: w.start)
    absorbed: set[int] = set()
    merged = 0
    for index, workout in enumerate(workouts):
        if workout.id in absorbed:
            continue
        for other in workouts[index + 1:]:
            # Sorted by start, so once one starts after this one ends, none later can overlap.
            if other.start > workout.start + max(workout.duration, MINIMUM_DURATION):
                break
            if other.id in absorbed or not workout.apps.isdisjoint(other.apps):
                continue
            if overlap_score(workout.kind, workout.start, workout.duration, workout.distance,
                             other.kind, other.start, other.duration, other.distance) is None:
                continue
            conn.execute("UPDATE workout_sources SET workout_id=? WHERE workout_id=?", (workout.id, other.id))
            conn.execute("DELETE FROM workouts WHERE id=?", (other.id,))
            workout.apps |= other.apps
            absorbed.add(other.id)
            merged += 1
            recompute(conn, workout.id)
    conn.commit()
    return merged

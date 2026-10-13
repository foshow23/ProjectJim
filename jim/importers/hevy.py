"""Hevy's "Export Workouts" CSV. Each row is one set; rows sharing a start time are one workout.

In Hevy: Profile -> Settings -> Export & Import Data -> Export Workouts.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from .. import db, merger
from ..models import HEVY, SourceSet, SourceWorkout

APP_NAME = "Hevy"
POUNDS_TO_KILOGRAMS = 0.45359237
# Hevy writes local times like "7 Oct 2026, 11:27".
DATE_FORMATS = ["%d %b %Y, %H:%M", "%d %b %Y, %I:%M %p", "%Y-%m-%d %H:%M:%S"]


class NotHevyExport(ValueError):
    def __init__(self):
        super().__init__("That doesn't look like a Hevy workout export. In Hevy, go to Profile → Settings → "
                         "Export & Import Data → Export Workouts.")


@dataclass
class HevyResult:
    workouts: int = 0
    sets: int = 0
    ingest: merger.IngestResult = field(default_factory=merger.IngestResult)
    removed: int = 0            # in ProjectJim but no longer in the export (deleted in Hevy)
    uses_pounds: bool = False


def parse_date(text: str) -> float | None:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).timestamp()
        except ValueError:
            continue
    return None


def _number(row: dict, column: str) -> float | None:
    value = (row.get(column) or "").strip()
    try:
        return float(value) if value else None
    except ValueError:
        return None


def parse(text: str) -> tuple[list[SourceWorkout], bool]:
    """Returns the workouts (in file order) and whether weights are in pounds."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    columns = {name.strip().lower() for name in reader.fieldnames or []}
    if not {"title", "start_time", "exercise_title"} <= columns:
        raise NotHevyExport()
    # Hevy names the weight and distance columns after the units set in the app.
    uses_pounds = "weight_lbs" in columns

    workouts: dict[str, SourceWorkout] = {}
    last_exercise: dict[str, str] = {}
    exercise_index: dict[str, int] = {}
    for raw in reader:
        row = {(key or "").strip().lower(): value for key, value in raw.items()}
        start_text = (row.get("start_time") or "").strip()
        start = parse_date(start_text)
        if start is None:
            continue
        if start_text not in workouts:
            end = parse_date(row.get("end_time") or "")
            workouts[start_text] = SourceWorkout(
                source=HEVY,
                # Start times are unique per Hevy account and, unlike titles, don't change on rename.
                external_id=str(int(start)), app_name=APP_NAME, kind="strength",
                title=(row.get("title") or "").strip() or None, start_ts=start,
                duration=max(0.0, end - start) if end else 0.0, sets=[],
            )
        exercise = (row.get("exercise_title") or "").strip()
        if not exercise:
            continue
        if last_exercise.get(start_text) != exercise:
            exercise_index[start_text] = exercise_index.get(start_text, -1) + 1
            last_exercise[start_text] = exercise

        weight = _number(row, "weight_kg")
        if weight is None and (pounds := _number(row, "weight_lbs")) is not None:
            weight = pounds * POUNDS_TO_KILOGRAMS
        distance = None
        if (km := _number(row, "distance_km")) is not None:
            distance = km * 1000
        elif (miles := _number(row, "distance_miles")) is not None:
            distance = miles * 1609.344

        sets = workouts[start_text].sets
        index = _number(row, "set_index")
        reps = _number(row, "reps")
        superset = _number(row, "superset_id")
        sets.append(SourceSet(
            exercise_index=exercise_index[start_text], exercise_title=exercise,
            set_index=int(index) if index is not None else len(sets),
            set_type=(row.get("set_type") or "").strip() or "normal",
            weight_kg=weight, reps=int(reps) if reps is not None else None, distance_m=distance,
            duration_s=_number(row, "duration_seconds"), rpe=_number(row, "rpe"),
            superset_id=int(superset) if superset is not None else None,
        ))
    return list(workouts.values()), uses_pounds


def import_csv(conn: sqlite3.Connection, text: str) -> HevyResult:
    workouts, uses_pounds = parse(text)
    if not workouts:
        raise ValueError("That Hevy export doesn't contain any workouts.")
    result = HevyResult(workouts=len(workouts), sets=sum(len(w.sets or []) for w in workouts),
                        uses_pounds=uses_pounds)
    result.ingest = merger.ingest(conn, workouts)

    # Hevy exports everything, so a workout missing from the file's date range was deleted in
    # Hevy. Outside that range, leave things alone in case this is an older or partial file.
    earliest, latest = min(w.start_ts for w in workouts), max(w.start_ts for w in workouts)
    ids = {w.external_id for w in workouts}
    result.removed = merger.remove_sources(
        conn, HEVY, lambda r: earliest <= r["start_ts"] <= latest and r["external_id"] not in ids)
    merger.consolidate(conn)
    db.set_setting(conn, "weight_unit", "lb" if uses_pounds else "kg")
    return result

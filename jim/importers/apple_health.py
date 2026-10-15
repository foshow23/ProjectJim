"""Apple Health's export (Health app -> profile picture -> Export All Health Data -> export.zip).

A Python app can't read Apple Health live, so this reads the export instead, but only for body
measurements: weight, body fat, sleep, resting heart rate, HRV, VO2 max, steps and active calories.
Workouts come straight from Garmin and Hevy, and food from Lose It!, which carry more detail than
the copies those apps write to Health. The export is the whole history, so each import replaces
what the previous one brought in.
"""

from __future__ import annotations

import sqlite3
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from .. import db, merger
from ..models import APPLE_HEALTH

PREFIX = "HKQuantityTypeIdentifier"
SLEEP = "HKCategoryTypeIdentifierSleepAnalysis"

# Health record type -> our metric name, for single measurements.
SAMPLE_METRICS = {"BodyMass": "weight", "BodyFatPercentage": "body_fat", "VO2Max": "vo2max"}
# Averaged per day.
DAILY_AVERAGE_METRICS = {"RestingHeartRate": "resting_hr", "HeartRateVariabilitySDNN": "hrv"}
# Summed per day (taking one source per day, so a watch and a phone aren't both counted).
DAILY_SUM_METRICS = {"StepCount": "steps", "ActiveEnergyBurned": "active_energy"}

# Health exports in whatever units the phone displays, so convert to ours.
_TO_KILOGRAMS = {"kg": 1.0, "g": 0.001, "lb": 0.45359237, "st": 6.35029318, "oz": 0.028349523125}
_TO_KCAL = {"kcal": 1.0, "Cal": 1.0, "kJ": 1 / 4.184, "cal": 0.001}


class NotAppleHealthExport(ValueError):
    def __init__(self):
        super().__init__("That isn't an Apple Health export. In the Health app, tap your profile picture, then "
                         "Export All Health Data, and import the export.zip it creates.")


@dataclass
class HealthResult:
    measurements: int = 0
    sources: list[str] = field(default_factory=list)


def is_apple_health_export(archive: zipfile.ZipFile) -> bool:
    return any(name.endswith("export.xml") for name in archive.namelist())


def _timestamp(text: str | None) -> float | None:
    """ "2026-10-04 07:51:02 -0400" -> seconds since 1970."""
    try:
        return datetime.strptime(text or "", "%Y-%m-%d %H:%M:%S %z").timestamp()
    except ValueError:
        return None


def _day_start(day: str) -> float:
    return datetime.strptime(day, "%Y-%m-%d").timestamp()


def _convert(value: str | None, unit: str | None, table: dict[str, float]) -> float | None:
    try:
        return float(value) * table[unit or ""]
    except (TypeError, ValueError, KeyError):
        return None


def _float(value: str | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sleep_hours(intervals: list[tuple[float, float]]) -> float:
    """Hours covered by the intervals, with overlaps merged so a watch and a phone recording the
    same night aren't counted twice."""
    total, current = 0.0, None
    for start, end in sorted(intervals):
        if current and start <= current[1]:
            current = (current[0], max(current[1], end))
        else:
            if current:
                total += current[1] - current[0]
            current = (start, end)
    if current:
        total += current[1] - current[0]
    return total / 3600


def import_export(conn: sqlite3.Connection, xml_file) -> HealthResult:
    """Reads export.xml (a file object). It can be hundreds of MB, so it's streamed, not loaded."""
    samples: list[tuple[str, float, float, str]] = []                       # metric, ts, value, source
    averages: dict[tuple[str, str], list[float]] = defaultdict(list)       # (metric, day) -> values
    sums: dict[tuple[str, str, str], float] = defaultdict(float)           # (metric, day, source) -> total
    sleep: dict[str, list[tuple[float, float]]] = defaultdict(list)        # wake-up day -> asleep intervals
    sources: set[str] = set()
    saw_health_data = False

    context = ET.iterparse(xml_file, events=("start", "end"))
    _, root = next(context)
    saw_health_data = root.tag == "HealthData"
    for event, element in context:
        if event != "end" or element.tag != "Record":
            continue
        record_type = element.get("type") or ""
        source = element.get("sourceName") or "Apple Health"
        day = (element.get("startDate") or "")[:10]
        name = record_type.removeprefix(PREFIX)
        if record_type == SLEEP:
            if "Asleep" in (element.get("value") or ""):
                start, end = _timestamp(element.get("startDate")), _timestamp(element.get("endDate"))
                if start is not None and end is not None:
                    sleep[(element.get("endDate") or "")[:10]].append((start, end))
                    sources.add(source)
        elif name in SAMPLE_METRICS:
            ts = _timestamp(element.get("endDate"))
            if name == "BodyMass":
                value = _convert(element.get("value"), element.get("unit"), _TO_KILOGRAMS)
            else:
                value = _float(element.get("value"))
            if ts is not None and value is not None:
                samples.append((SAMPLE_METRICS[name], ts, value, source))
                sources.add(source)
        elif name in DAILY_AVERAGE_METRICS:
            if (value := _float(element.get("value"))) is not None:
                averages[(DAILY_AVERAGE_METRICS[name], day)].append(value)
                sources.add(source)
        elif name in DAILY_SUM_METRICS:
            table = _TO_KCAL if name == "ActiveEnergyBurned" else {"count": 1.0}
            if (value := _convert(element.get("value"), element.get("unit"), table)) is not None:
                sums[(DAILY_SUM_METRICS[name], day, source)] += value
        root.clear()    # free what's been read

    if not saw_health_data:
        raise NotAppleHealthExport()

    result = HealthResult(sources=sorted(sources))

    # Earlier versions also imported Health's copies of workouts; drop any still here.
    merger.remove_sources(conn, APPLE_HEALTH, lambda r: True)

    # Body measurements: replace everything from the previous import. Two apps can write the
    # same weigh-in to Health (a scale's app and Garmin Connect), so keep one per minute.
    conn.execute("DELETE FROM body_metrics")
    unique = {(metric, int(ts // 60)): (metric, ts, value, source) for metric, ts, value, source in reversed(samples)}
    rows = sorted(unique.values(), key=lambda row: row[1])
    rows += [(metric, _day_start(day), sum(values) / len(values), "Apple Health") for (metric, day), values in averages.items()]
    # One source per day (the one that counted the most), so two devices aren't added together.
    best: dict[tuple[str, str], tuple[float, str]] = {}
    for (metric, day, source), total in sums.items():
        if total > best.get((metric, day), (0.0, ""))[0]:
            best[(metric, day)] = (total, source)
    rows += [(metric, _day_start(day), total, source) for (metric, day), (total, source) in best.items()]
    rows += [("sleep_hours", _day_start(day), sleep_hours(intervals), "Apple Health")
             for day, intervals in sleep.items() if day]
    conn.executemany("INSERT OR REPLACE INTO body_metrics (metric, ts, value, source) VALUES (?, ?, ?, ?)", rows)
    result.measurements = len(rows)

    conn.commit()
    db.set_setting(conn, "apple_health_import", {"sources": result.sources, "at": time.time()})
    return result


def import_archive(conn: sqlite3.Connection, archive: zipfile.ZipFile) -> HealthResult:
    name = next((n for n in archive.namelist() if n.endswith("export.xml")), None)
    if name is None:
        raise NotAppleHealthExport()
    with archive.open(name) as xml_file:
        return import_export(conn, xml_file)

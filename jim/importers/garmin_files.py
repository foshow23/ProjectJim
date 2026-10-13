"""Activity files from Garmin Connect (or any app that writes them): GPX, TCX and FIT.

GPX carries the GPS track, TCX adds lap totals like calories, and FIT is the watch's own
recording with everything. Importing more than one file for the same activity fills in one record.
"""

from __future__ import annotations

import io
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone

import fitdecode

from .. import merger
from ..models import (GARMIN, KIND_NAMES, RoutePoint, SourceWorkout, decode_route, gps_distance, has_gps,
                      kinds_can_merge, route_average_hr, route_detail, route_elevation_gain)

APP_NAME = "Garmin Connect"
SEMICIRCLES_TO_DEGREES = 180 / 2**31


class NotAnActivityFile(ValueError):
    pass


@dataclass
class FileResult:
    title: str
    kind: str
    has_gps: bool
    has_heart_rate: bool
    matched_existing: bool      # joined a workout another source already had


# --- Kinds ------------------------------------------------------------------------------------

def kind_for(activity_type: str | None) -> str:
    """Garmin's GPX types ("running", "indoor_cycling"), TCX sports ("Biking") and FIT sports."""
    text = (activity_type or "").lower()
    if "strength" in text:
        return "strength"
    if "run" in text:
        return "run"
    if "cycl" in text or "bik" in text or "ride" in text:
        return "ride"
    if "hik" in text:
        return "hike"
    if "walk" in text:
        return "walk"
    if "swim" in text:
        return "swim"
    return "other"


# --- Activity IDs -----------------------------------------------------------------------------

def key_for_activity_id(activity_id: int) -> str:
    """The record key for a Garmin activity ID. Records without a known ID are keyed by start time."""
    return f"garmin-{activity_id}"


def _activity_id_in_key(key: str) -> int | None:
    return int(key[7:]) if key.startswith("garmin-") and key[7:].isdigit() else None


def activity_id_from_filename(name: str) -> int | None:
    """Garmin's own file names carry the activity ID: "activity_22777292791.tcx" from Export to
    TCX/GPX, "22777292791.zip" / "22777292791_ACTIVITY.fit" from Export Original. Other FIT names
    (like the upload IDs inside the data export) aren't activity IDs."""
    base = name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    for pattern in (r"activity_(\d+)$", r"(\d+)_ACTIVITY$"):
        if match := re.fullmatch(pattern, base):
            return int(match.group(1))
    if name.lower().endswith(".zip") and base.isdigit():
        return int(base)
    return None


# --- Parsing ----------------------------------------------------------------------------------

def _parse_time(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _local(tag: str) -> str:
    """Element name without its XML namespace: "{http://...}trkpt" -> "trkpt"."""
    return tag.rsplit("}", 1)[-1]


def _float(text: str | None) -> float | None:
    try:
        return float(text) if text is not None else None
    except ValueError:
        return None


def parse(data: bytes) -> SourceWorkout:
    """GPX, TCX or FIT, told apart by content rather than file name."""
    if len(data) >= 12 and data[8:12] == b".FIT":
        return parse_fit(data)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise NotAnActivityFile("That file isn't a readable GPX, TCX or FIT file.") from error
    kind = _local(root.tag)
    if kind == "gpx":
        return _parse_gpx(root)
    if kind == "TrainingCenterDatabase":
        return _parse_tcx(root)
    raise NotAnActivityFile("That file isn't a readable GPX, TCX or FIT file.")


def _finish(*, creator: str, name: str | None, activity_type: str | None, start: float | None,
            samples: list[tuple[float | None, float | None, float | None, float | None, float | None]],
            lap_seconds: float = 0, lap_distance: float = 0, lap_calories: float = 0,
            lap_hr: float | None = None) -> SourceWorkout:
    """Builds the workout from samples of (time, lat, lon, elevation, heart rate)."""
    times = [s[0] for s in samples if s[0] is not None]
    start = times[0] if times else start
    if start is None:
        raise NotAnActivityFile("That file doesn't contain an activity with a start time.")
    route = [RoutePoint(lat, lon, ele, (t - start) if t is not None else 0.0, hr) for t, lat, lon, ele, hr in samples]
    distance = gps_distance(route)
    return SourceWorkout(
        source=GARMIN, external_id=str(int(start)), app_name=creator, kind=kind_for(activity_type),
        title=name or None, start_ts=start,
        # TCX lap time is the watch's timer, which excludes pauses; GPX only has first-to-last.
        duration=lap_seconds if lap_seconds > 0 else (times[-1] - start if times else 0.0),
        distance=lap_distance if lap_distance > 0 else (distance if distance > 0 else None),
        calories=lap_calories if lap_calories > 0 else None,
        avg_hr=route_average_hr(route) or lap_hr,
        elevation_gain=route_elevation_gain(route) if has_gps(route) else None,
        route=route,
    )


def _parse_gpx(root: ET.Element) -> SourceWorkout:
    name = activity_type = None
    start = None
    samples = []
    for element in root.iter():
        tag = _local(element.tag)
        if tag == "metadata":
            start = next((_parse_time(c.text) for c in element if _local(c.tag) == "time"), None)
        elif tag == "trk":
            for child in element:
                if _local(child.tag) == "name":
                    name = (child.text or "").strip()
                elif _local(child.tag) == "type":
                    activity_type = (child.text or "").strip()
        elif tag == "trkpt":
            time = elevation = heart_rate = None
            for child in element.iter():
                child_tag = _local(child.tag)
                if child_tag == "time":
                    time = _parse_time(child.text)
                elif child_tag == "ele":
                    elevation = _float(child.text)
                elif child_tag == "hr":      # Garmin's TrackPointExtension, whatever its prefix
                    heart_rate = _float(child.text)
            samples.append((time, _float(element.get("lat")), _float(element.get("lon")), elevation, heart_rate))
    return _finish(creator=root.get("creator") or APP_NAME, name=name, activity_type=activity_type,
                   start=start, samples=samples)


def _parse_tcx(root: ET.Element) -> SourceWorkout:
    activity_type = None
    start = None
    lap_seconds = lap_distance = lap_calories = 0.0
    lap_heart_rates: list[tuple[float, float]] = []    # (bpm, seconds) per lap
    samples = []
    for element in root.iter():
        tag = _local(element.tag)
        if tag == "Activity":
            activity_type = element.get("Sport")
        elif tag == "Lap":
            start = start or _parse_time(element.get("StartTime"))
            seconds = 0.0
            for child in element:
                child_tag = _local(child.tag)
                if child_tag == "TotalTimeSeconds":
                    seconds = _float(child.text) or 0.0
                    lap_seconds += seconds
                elif child_tag == "DistanceMeters":
                    lap_distance += _float(child.text) or 0.0
                elif child_tag == "Calories":
                    lap_calories += _float(child.text) or 0.0
                elif child_tag == "AverageHeartRateBpm":
                    bpm = next((_float(v.text) for v in child if _local(v.tag) == "Value"), None)
                    if bpm:
                        lap_heart_rates.append((bpm, max(seconds, 1.0)))
        elif tag == "Trackpoint":
            time = lat = lon = elevation = heart_rate = None
            for child in element.iter():
                child_tag = _local(child.tag)
                if child_tag == "Time":
                    time = _parse_time(child.text)
                elif child_tag == "LatitudeDegrees":
                    lat = _float(child.text)
                elif child_tag == "LongitudeDegrees":
                    lon = _float(child.text)
                elif child_tag == "AltitudeMeters":
                    elevation = _float(child.text)
                elif child_tag == "HeartRateBpm":
                    heart_rate = next((_float(v.text) for v in child if _local(v.tag) == "Value"), None)
            samples.append((time, lat, lon, elevation, heart_rate))
    lap_hr = None
    if lap_heart_rates:
        lap_hr = sum(bpm * seconds for bpm, seconds in lap_heart_rates) / sum(s for _, s in lap_heart_rates)
    return _finish(creator=APP_NAME, name=None, activity_type=activity_type, start=start, samples=samples,
                   lap_seconds=lap_seconds, lap_distance=lap_distance, lap_calories=lap_calories, lap_hr=lap_hr)


def is_fit_activity(data: bytes) -> bool:
    """Cheaply checks a FIT file's type by reading only its first message.

    Garmin's export holds thousands of daily monitoring files; this skips them quickly.
    """
    try:
        with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.DISABLED) as reader:
            for frame in reader:
                if isinstance(frame, fitdecode.FitDataMessage) and frame.name == "file_id":
                    return frame.get_value("type", fallback=None) == "activity"
    except fitdecode.FitError:
        pass
    return False


def parse_fit(data: bytes) -> SourceWorkout:
    """The watch's own recording: a session summary plus per-second records."""
    session = None
    samples = []
    try:
        with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.DISABLED) as reader:
            for frame in reader:
                if not isinstance(frame, fitdecode.FitDataMessage):
                    continue
                if frame.name == "session" and session is None:
                    session = frame
                elif frame.name == "record":
                    timestamp = frame.get_value("timestamp", fallback=None)
                    if timestamp is None:
                        continue
                    lat = frame.get_value("position_lat", fallback=None)
                    lon = frame.get_value("position_long", fallback=None)
                    elevation = frame.get_value("enhanced_altitude", fallback=None)
                    if elevation is None:
                        elevation = frame.get_value("altitude", fallback=None)
                    samples.append((
                        timestamp.replace(tzinfo=timezone.utc).timestamp(),
                        lat * SEMICIRCLES_TO_DEGREES if lat is not None else None,
                        lon * SEMICIRCLES_TO_DEGREES if lon is not None else None,
                        elevation, frame.get_value("heart_rate", fallback=None),
                    ))
    except fitdecode.FitError as error:
        raise NotAnActivityFile("That FIT file couldn't be read.") from error
    if session is None:
        raise NotAnActivityFile("That FIT file isn't an activity recording.")

    def value(name):
        return session.get_value(name, fallback=None)

    start_time = value("start_time")
    start = start_time.replace(tzinfo=timezone.utc).timestamp() if start_time else (samples[0][0] if samples else None)
    if start is None:
        raise NotAnActivityFile("That FIT file doesn't contain an activity with a start time.")
    route = [RoutePoint(lat, lon, ele, t - start, hr) for t, lat, lon, ele, hr in samples]
    sub_sport, sport = str(value("sub_sport") or ""), str(value("sport") or "")
    kind = "strength" if "strength" in sub_sport else kind_for(sport)
    return SourceWorkout(
        source=GARMIN, external_id=str(int(start)), app_name=APP_NAME, kind=kind, title=None, start_ts=start,
        duration=value("total_timer_time") or value("total_elapsed_time") or 0.0,
        distance=value("total_distance") or None, calories=value("total_calories") or None,
        avg_hr=value("avg_heart_rate") or route_average_hr(route),
        elevation_gain=value("total_ascent") if value("total_ascent") is not None
        else (route_elevation_gain(route) if has_gps(route) else None),
        route=route,
    )


# --- Matching against what's already saved ----------------------------------------------------

def reconcile(conn: sqlite3.Connection, items: list[SourceWorkout]) -> None:
    """Lines up incoming Garmin records with ones already saved for the same activity, so a GPX,
    a TCX and the full export of one run all update one record instead of adding copies.

    Matching, most reliable first:
    1. Same Garmin activity ID (survives start-time edits in Garmin Connect).
    2. Start times within a minute (files of one activity can disagree by a second).
    3. Overlapping time, same kind, similar distance (catches small start-time edits).
    Two records with different activity IDs are never matched.

    A match takes the saved record's key (or upgrades the saved key to the activity ID) and keeps
    whatever detail the saved record has that the new one lacks, such as a GPS track.
    """
    saved = [dict(row) for row in conn.execute("SELECT * FROM workout_sources WHERE source=?", (GARMIN,))]
    if not saved:
        return
    by_key = {row["external_id"]: row for row in saved}
    claimed: set[int] = set()

    for item in items:
        existing = by_key.get(item.external_id) or _closest_match(item, saved, claimed)
        if existing is None:
            continue
        claimed.add(existing["id"])

        if _activity_id_in_key(item.external_id) is not None and _activity_id_in_key(existing["external_id"]) is None:
            by_key.pop(existing["external_id"], None)
            conn.execute("UPDATE workout_sources SET external_id=? WHERE id=?", (item.external_id, existing["id"]))
            existing["external_id"] = item.external_id
            by_key[item.external_id] = existing
        else:
            item.external_id = existing["external_id"]

        if item.kind == "other":
            item.kind = existing["kind"]
        if item.duration == 0:
            item.duration = existing["duration"]
        item.title = item.title or existing["title"]
        item.distance = item.distance or existing["distance"]
        item.calories = item.calories or existing["calories"]
        item.avg_hr = item.avg_hr or existing["avg_hr"]
        if item.elevation_gain is None:
            item.elevation_gain = existing["elevation_gain"]
        if route_detail(decode_route(existing["route"])) > route_detail(item.route or []):
            item.route = None     # keep the saved, more detailed track


def _closest_match(item: SourceWorkout, saved: list[dict], claimed: set[int]) -> dict | None:
    item_id = _activity_id_in_key(item.external_id)
    matches = []
    for candidate in saved:
        if candidate["id"] in claimed:
            continue
        candidate_id = _activity_id_in_key(candidate["external_id"])
        if item_id is not None and candidate_id is not None and item_id != candidate_id:
            continue    # two different Garmin activities
        gap = abs(candidate["start_ts"] - item.start_ts)
        if gap <= 60:
            if kinds_can_merge(item.kind, candidate["kind"]):
                matches.append((gap, candidate))
        elif merger.overlap_score(item.kind, item.start_ts, item.duration, item.distance, candidate["kind"],
                                  candidate["start_ts"], candidate["duration"], candidate["distance"]) is not None:
            matches.append((gap, candidate))
    return min(matches, key=lambda pair: pair[0])[1] if matches else None


def import_file(conn: sqlite3.Connection, data: bytes, filename: str | None = None) -> FileResult:
    """Imports one GPX, TCX or FIT file."""
    item = parse(data)
    if filename and (activity_id := activity_id_from_filename(filename)) is not None:
        item.external_id = key_for_activity_id(activity_id)
    reconcile(conn, [item])
    result = merger.ingest(conn, [item])
    merger.consolidate(conn)
    return FileResult(title=item.title or KIND_NAMES[item.kind], kind=item.kind, has_gps=has_gps(item.route or []),
                      has_heart_rate=item.avg_hr is not None, matched_existing=result.merged_into_existing > 0)

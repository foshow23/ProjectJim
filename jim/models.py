"""The shapes every importer converts its data into, so the merger treats all sources alike."""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass, field
from math import asin, cos, radians, sin, sqrt

KINDS = ["run", "walk", "hike", "ride", "swim", "strength", "other"]
KIND_NAMES = {"run": "Run", "walk": "Walk", "hike": "Hike", "ride": "Ride", "swim": "Swim",
              "strength": "Strength", "other": "Workout"}
KIND_PLURALS = {"run": "Runs", "walk": "Walks", "hike": "Hikes", "ride": "Rides", "swim": "Swims",
                "strength": "Strength", "other": "Other"}

# Where a record came from.
APPLE_HEALTH = "apple_health"   # anything in an Apple Health export: Garmin Connect, Hevy, a watch...
HEVY = "hevy"                   # Hevy's CSV export
GARMIN = "garmin"               # Garmin GPX/TCX/FIT files and Garmin's data export
SOURCE_NAMES = {APPLE_HEALTH: "Apple Health", HEVY: "Hevy export", GARMIN: "Garmin file"}


def kinds_can_merge(a: str, b: str) -> bool:
    """Whether two sources' records of these kinds can be the same session.

    "other" matches anything (some apps don't say what the workout was), and apps disagree
    on whether a hike is a walk.
    """
    on_foot = {"walk", "hike"}
    return a == b or "other" in (a, b) or (a in on_foot and b in on_foot)


@dataclass
class RoutePoint:
    """One recorded sample. Indoor activities have samples without coordinates (heart rate only)."""
    lat: float | None
    lon: float | None
    elevation: float | None     # meters
    elapsed: float              # seconds since the start
    heart_rate: float | None


@dataclass
class SourceSet:
    """One set of one exercise, as logged in Hevy."""
    exercise_index: int
    exercise_title: str
    set_index: int
    set_type: str = "normal"
    weight_kg: float | None = None
    reps: int | None = None
    distance_m: float | None = None
    duration_s: float | None = None
    rpe: float | None = None
    superset_id: int | None = None


@dataclass
class SourceWorkout:
    """A workout as one source describes it, before merging."""
    source: str
    external_id: str
    app_name: str
    kind: str
    title: str | None
    start_ts: float
    duration: float
    distance: float | None = None
    calories: float | None = None
    avg_hr: float | None = None
    elevation_gain: float | None = None
    # None means "this source doesn't track it", so whatever is stored is left alone.
    sets: list[SourceSet] | None = None
    route: list[RoutePoint] | None = field(default=None, repr=False)


# --- Routes -----------------------------------------------------------------------------------

def encode_route(points: list[RoutePoint]) -> bytes:
    rows = [[p.lat, p.lon, p.elevation, p.elapsed, p.heart_rate] for p in points]
    return zlib.compress(json.dumps(rows, separators=(",", ":")).encode())


def decode_route(blob: bytes | None) -> list[RoutePoint]:
    if not blob:
        return []
    return [RoutePoint(*row) for row in json.loads(zlib.decompress(blob))]


def has_gps(points: list[RoutePoint]) -> bool:
    return any(p.lat is not None and p.lon is not None for p in points)


def route_detail(points: list[RoutePoint]) -> int:
    """Ranks tracks: GPS beats heart-rate-only, then more samples beats fewer."""
    return (1_000_000_000 if has_gps(points) else 0) + len(points)


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Meters between two coordinates."""
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * 6_371_000 * asin(sqrt(a))


def gps_distance(points: list[RoutePoint]) -> float:
    coords = [(p.lat, p.lon) for p in points if p.lat is not None and p.lon is not None]
    return sum(_haversine(*a, *b) for a, b in zip(coords, coords[1:]))


def route_elevation_gain(points: list[RoutePoint]) -> float | None:
    """Meters climbed, ignoring wiggles under 2 m so GPS altitude noise doesn't add up."""
    elevations = [p.elevation for p in points if p.elevation is not None]
    if not elevations:
        return None
    gain, reference = 0.0, elevations[0]
    for elevation in elevations[1:]:
        if elevation - reference >= 2:
            gain += elevation - reference
            reference = elevation
        elif elevation < reference:
            reference = elevation
    return gain


def route_average_hr(points: list[RoutePoint]) -> float | None:
    rates = [p.heart_rate for p in points if p.heart_rate is not None]
    return sum(rates) / len(rates) if rates else None

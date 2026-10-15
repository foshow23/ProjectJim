"""Workout rows and the workout detail screen, shared by the Cardio and Strength tabs."""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime

import altair as alt
import pandas as pd
import pydeck as pdk
import streamlit as st

from . import bodyweight, db, formatting, ios
from .models import APPLE_HEALTH, SOURCE_NAMES, RoutePoint, decode_route, has_gps

KIND_ICONS = {"run": "directions_run", "walk": "directions_walk", "hike": "hiking", "ride": "directions_bike",
              "swim": "pool", "strength": "fitness_center", "other": "local_fire_department"}
MAP_CREDIT = "Map © OpenStreetMap contributors, © CARTO"


# --- List rows --------------------------------------------------------------------------------

def strength_stats(conn: sqlite3.Connection) -> dict[int, dict]:
    """Exercises and volume (weight × reps, warm-ups excluded) for every workout with sets.
    Bodyweight sets count your body weight on that day (see jim/bodyweight.py)."""
    weights = bodyweight.weigh_ins(conn)
    stats = {}      # workout id -> {"exercises": ..., "volume": ...}
    for row in conn.execute("SELECT s.workout_id, w.start_ts, e.exercise_index, e.exercise_title, e.set_type,"
                            " e.weight_kg, e.reps FROM exercise_sets e JOIN workout_sources s ON s.id = e.source_id"
                            " JOIN workouts w ON w.id = s.workout_id"):
        if row["workout_id"] not in stats:
            stats[row["workout_id"]] = {"exercises": set(), "volume": 0.0}
        workout = stats[row["workout_id"]]
        workout["exercises"].add(row["exercise_index"])     # a set, so each exercise counts once
        if row["set_type"] != "warmup" and row["reps"]:
            body = bodyweight.body_weight_on(weights, row["start_ts"])
            workout["volume"] += (bodyweight.set_load(row["exercise_title"], row["weight_kg"], body) or 0) * row["reps"]
    # Swap each set of exercises for how many there were.
    for workout in stats.values():
        workout["exercises"] = len(workout["exercises"])
    return stats


def summary(workout: sqlite3.Row, stats: dict | None, unit: str) -> str:
    """ "5.02 km · 5:24 /km · 27:06 · 152 bpm" or "6 exercises · 10,756 lb · 48:00"."""
    parts = []
    if workout["kind"] == "strength":
        if stats:
            parts.append(f"{stats['exercises']} exercises")
            if stats["volume"]:
                parts.append(formatting.weight(stats["volume"], unit, grouped=True))
    elif workout["distance"]:
        parts += [formatting.distance(workout["distance"]), formatting.pace(workout["distance"], workout["duration"])]
    if workout["duration"]:
        parts.append(formatting.duration(workout["duration"]))
    if workout["avg_hr"]:
        parts.append(f"{workout['avg_hr']:.0f} bpm")
    return " · ".join(parts)


def row(workout: sqlite3.Row, stats: dict | None = None, unit: str = "kg") -> str:
    """A workout in a list: icon, name, date, and its numbers underneath."""
    return ios.row(
        workout["title"], summary(workout, stats, unit), id=str(workout["id"]), kind="headline",
        icon_name=KIND_ICONS.get(workout["kind"], "local_fire_department"),
        tint="purple" if workout["kind"] == "strength" else "orange",
        detail=formatting.short_date(workout["start_ts"]),
    )


def show_list(tab: str, workouts: list[sqlite3.Row], stats: dict[int, sqlite3.Row], unit: str) -> None:
    """The tappable list of workouts. Tapping one pushes its detail."""
    html = ios.section([row(w, stats.get(w["id"]), unit) for w in workouts])
    tapped = ios.tap(html, key=f"{tab}_list", restore_scroll=ios.restoring_scroll(tab))
    if tapped:
        ios.push(tab, int(tapped))


# --- Detail -----------------------------------------------------------------------------------

def best_route(conn: sqlite3.Connection, workout_id: int) -> list[RoutePoint]:
    """The most detailed recorded track among the workout's sources."""
    blobs = [row["route"] for row in conn.execute(
        "SELECT route FROM workout_sources WHERE workout_id=? AND route IS NOT NULL", (workout_id,))]
    return decode_route(max(blobs, key=len)) if blobs else []


def facts(workout: sqlite3.Row) -> str:
    moment = datetime.fromtimestamp(workout["start_ts"])
    items = [ios.row("Date", value=f"{moment:%A, %b} {moment.day} at {moment:%-I:%M %p}")]
    if workout["duration"]:
        items.append(ios.row("Duration", value=formatting.duration(workout["duration"])))
    if workout["distance"]:
        items.append(ios.row("Distance", value=formatting.distance(workout["distance"])))
        items.append(ios.row("Pace", value=formatting.pace(workout["distance"], workout["duration"])))
    if workout["avg_hr"]:
        items.append(ios.row("Average heart rate", value=f"{workout['avg_hr']:.0f} bpm"))
    if workout["calories"]:
        items.append(ios.row("Active calories", value=f"{workout['calories']:.0f} kcal"))
    if workout["elevation_gain"]:
        items.append(ios.row("Elevation gain", value=f"{workout['elevation_gain']:.0f} m"))
    return ios.section(items)


def fit_view(path: list[list[float]], width: int = 300, height: int = 240) -> pdk.ViewState:
    """Centers the map on the route, zoomed so all of it shows in a map of this size (pixels).

    Web maps double in scale with each zoom level, starting from a 512-pixel-wide world at
    zoom 0, so the zoom that fits a span is log2(pixels available / pixels the span takes at
    zoom 0). North-south uses the Mercator projection's stretched latitude.
    """
    lons, lats = [point[0] for point in path], [point[1] for point in path]

    def mercator(latitude: float) -> float:
        return math.log(math.tan(math.pi / 4 + math.radians(latitude) / 2))

    across = max(max(lons) - min(lons), 1e-4) / 360 * 512
    down = max(mercator(max(lats)) - mercator(min(lats)), 1e-6) / (2 * math.pi) * 512
    zoom = min(math.log2(width / across), math.log2(height / down), 17)
    return pdk.ViewState(longitude=(min(lons) + max(lons)) / 2, latitude=(min(lats) + max(lats)) / 2, zoom=zoom)


def show_map(route: list[RoutePoint]) -> None:
    """The GPS track on a map, with start (green) and finish (red) markers."""
    path = [[p.lon, p.lat] for p in route if p.lat is not None and p.lon is not None]
    if len(path) < 2:
        return
    ends = pd.DataFrame([{"position": path[0], "color": [52, 199, 89]}, {"position": path[-1], "color": [255, 59, 48]}])
    with ios.card("map", flush=True, footnote=f"Green is the start, red the finish. {MAP_CREDIT}."):
        st.pydeck_chart(pdk.Deck(
            map_style=None,     # Streamlit's default basemap (CARTO); no API token needed
            initial_view_state=fit_view(path),
            # Drag to pan and use the buttons to zoom; scrolling moves the page, not the map.
            views=[pdk.View("MapView", controller={"scrollZoom": False, "dragRotate": False, "touchRotate": False})],
            layers=[
                pdk.Layer("PathLayer", data=[{"path": path}], get_path="path", get_color=[255, 149, 0],
                          width_min_pixels=4, cap_rounded=True, joint_rounded=True),
                pdk.Layer("ScatterplotLayer", data=ends, get_position="position", get_fill_color="color",
                          get_line_color=[255, 255, 255], stroked=True, radius_min_pixels=6, line_width_min_pixels=2),
            ],
        ), height=280)


def show_heart_rate(route: list[RoutePoint]) -> None:
    samples = [(p.elapsed / 60, p.heart_rate) for p in route if p.heart_rate is not None]
    if len(samples) < 2:
        return
    # Thin long recordings to ~300 points; the line looks the same and draws faster.
    samples = samples[::max(1, len(samples) // 300)]
    frame = pd.DataFrame(samples, columns=["minutes", "bpm"])
    with ios.card("heart", title="Heart Rate"):
        ios.chart(alt.Chart(frame).mark_line(color=ios.color("red"), interpolate="monotone", strokeWidth=2).encode(
            x=alt.X("minutes:Q", title="minutes"),
            y=alt.Y("bpm:Q", scale=alt.Scale(zero=False), title=None),
        ), height=170)


def exercise_sections(conn: sqlite3.Connection, workout_id: int) -> str:
    """Sets from whichever source logged them (Hevy), one section per exercise."""
    sets = conn.execute(
        "SELECT e.* FROM exercise_sets e JOIN workout_sources s ON s.id = e.source_id"
        " WHERE s.workout_id=? ORDER BY e.exercise_index, e.set_index", (workout_id,)).fetchall()
    unit = db.get_setting(conn, "weight_unit", "kg")
    start = conn.execute("SELECT start_ts FROM workouts WHERE id=?", (workout_id,)).fetchone()["start_ts"]
    body = bodyweight.body_weight_on(bodyweight.weigh_ins(conn), start)
    exercises: dict[int, list[sqlite3.Row]] = {}
    for item in sets:
        exercises.setdefault(item["exercise_index"], []).append(item)

    html = ""
    for items in exercises.values():
        lines, working = [], 0
        for item in items:
            warmup = item["set_type"] == "warmup"
            if not warmup:
                working += 1    # warm-ups show as "W"; working sets are numbered from 1
            detail = []
            notes = []
            load = bodyweight.set_load(item["exercise_title"], item["weight_kg"], body)
            if load:
                detail.append(formatting.weight(load, unit))
            if load and bodyweight.share_of_body_weight(item["exercise_title"]) is not None:
                notes.append("Bodyweight")
            if item["reps"] is not None:
                detail.append(f"× {item['reps']}" if detail else f"{item['reps']} reps")
            if item["distance_m"]:
                detail.append(formatting.distance(item["distance_m"]))
            if item["duration_s"]:
                detail.append(formatting.duration(item["duration_s"]))
            if item["rpe"]:
                detail.append(f"RPE {item['rpe']:g}")
            if item["set_type"] in ("failure", "dropset"):
                notes.append({"failure": "Failure", "dropset": "Drop set"}[item["set_type"]])
            lines.append(ios.row(" ".join(detail) or "—", leading="W" if warmup else str(working),
                                 leading_tint="orange" if warmup else "", detail=" · ".join(notes), kind="mono"))
        footnote = ""
        if body and bodyweight.share_of_body_weight(items[0]["exercise_title"]) is not None:
            footnote = f"Includes your body weight that day: {formatting.weight(body, unit)}."
        html += ios.section(lines, title=items[0]["exercise_title"], footnote=footnote)
    return html


def sources_section(conn: sqlite3.Connection, workout_id: int) -> str:
    """Where the workout's data came from, at the bottom of its detail."""
    records = conn.execute("SELECT * FROM workout_sources WHERE workout_id=? ORDER BY source", (workout_id,)).fetchall()
    items = []
    for record in records:
        label = f"{record['app_name']} via Apple Health" if record["source"] == APPLE_HEALTH \
            else SOURCE_NAMES.get(record["source"], record["source"])
        parts = [f"{datetime.fromtimestamp(record['start_ts']):%-I:%M %p}"]
        if record["duration"]:
            parts.append(formatting.duration(record["duration"]))
        if record["distance"]:
            parts.append(formatting.distance(record["distance"]))
        if record["avg_hr"]:
            parts.append(f"{record['avg_hr']:.0f} bpm")
        if record["calories"]:
            parts.append(f"{record['calories']:.0f} kcal")
        set_count = conn.execute("SELECT COUNT(*) FROM exercise_sets WHERE source_id=?", (record["id"],)).fetchone()[0]
        if set_count:
            parts.append(f"{set_count} sets")
        items.append(ios.row(label, " · ".join(parts), kind="small"))
    return ios.section(items, title="Sources",
                       footnote="Timing, heart rate and calories come from your watch when available; names and "
                                "sets from Garmin and Hevy.")


def show_detail(conn: sqlite3.Connection, tab: str, back: str) -> None:
    """The pushed screen for the workout this tab has open."""
    workout = conn.execute("SELECT * FROM workouts WHERE id=?", (ios.pushed(tab),)).fetchone()
    if workout is None:     # deleted by a newer import
        ios.pop(tab)
        st.rerun()
    if ios.nav_bar(workout["title"], back) == "back":
        ios.pop(tab)
        st.rerun()
    ios.scroll_to_top(f"workout-{workout['id']}")

    route = best_route(conn, workout["id"])
    if has_gps(route):
        show_map(route)
    ios.show(facts(workout))
    show_heart_rate(route)
    ios.show(exercise_sections(conn, workout["id"]) + sources_section(conn, workout["id"]))

"""Body tab: weight, heart, sleep and activity from Apple Health, which collects them from
Garmin Connect, Hevy, a scale or a watch."""

from datetime import date, datetime, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from jim import db, formatting, ios, sources_sheet, ui

conn = ui.connection()
unit = db.get_setting(conn, "weight_unit", "kg")


@st.dialog("Units")
def choose_units() -> None:
    names = {"kg": "Kilograms", "lb": "Pounds"}
    chosen = st.segmented_control("Weight Unit", list(names), default=unit, format_func=names.get, width="stretch")
    if st.button("Done", type="primary", width="stretch"):
        db.set_setting(conn, "weight_unit", chosen or unit)
        st.rerun()


action = ios.large_title("Body", [("units", "monitor_weight", "Units"), ("sources", "sync", "Sources")])
if action == "units":
    choose_units()
elif action == "sources":
    sources_sheet.show()


def series(metric: str) -> list[tuple[datetime, float]]:
    """Every measurement of a metric, oldest first."""
    return [(datetime.fromtimestamp(row["ts"]), row["value"]) for row in conn.execute(
        "SELECT ts, value FROM body_metrics WHERE metric=? ORDER BY ts", (metric,))]


def recent(points: list, days: int) -> list:
    """The last `days` days of a series, counted back from its newest measurement. (The data
    comes from an export, so "the last week" means the last week it covers.)"""
    if not points:
        return []
    start = points[-1][0] - timedelta(days=days)
    return [point for point in points if point[0] > start]


def average(points: list) -> float:
    return sum(value for _, value in points) / len(points) if points else 0.0


def day_name(moment: datetime, today: str, other: str = "") -> str:
    """ "Last night" / "Steps today" for today's data, otherwise the date."""
    return today if moment.date() == date.today() else f"{other}{moment:%b} {moment.day}"


def day_bars(points: list, tint: str) -> alt.Chart:
    """One bar per day, labeled with weekday letters."""
    frame = pd.DataFrame({"day": [moment.date().isoformat() for moment, _ in points], "value": [v for _, v in points]})
    return alt.Chart(frame).mark_bar(color=ios.color(tint), cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
        x=alt.X("day:O", axis=ios.day_axis(list(frame["day"]))), y=alt.Y("value:Q", title=None))


weight, body_fat = series("weight"), series("body_fat")
resting, hrv, vo2max = series("resting_hr"), series("hrv"), series("vo2max")
sleep, steps, energy = series("sleep_hours"), series("steps"), series("active_energy")

if not (weight or resting or sleep or steps):
    ios.show(ios.unavailable(
        "monitor_heart", "No Body Data Yet",
        "On your iPhone open Health, tap your profile picture, then Export All Health Data, and import the "
        "export.zip here. It brings in the weight, heart rate, sleep and steps that Garmin Connect, Hevy, a "
        "scale or a watch write to Apple Health."))
    if st.button("Import Apple Health Export", type="primary", width="stretch"):
        sources_sheet.show()
    st.stop()

if weight:
    latest_time, latest = weight[-1]
    month = recent(weight, 30)
    change = month[-1][1] - month[0][1] if len(month) > 1 else None
    trend = "" if change is None else (f'<span class="ios-small ios-secondary">{"+" if change >= 0 else "−"}'
                                       f'{formatting.weight(abs(change), unit)} in 30 days</span>')
    with ios.card("weight", title="Weight"):
        ios.show(f'<div class="ios-big"><span class="ios-big-number">{formatting.weight(latest, unit)}</span>'
                 f'<span class="ios-spacer"></span>{trend}</div>'
                 f'<div class="ios-caption">{latest_time:%b} {latest_time.day} at {latest_time:%-I:%M %p}</div>')
        ranges = {"30 Days": 30, "90 Days": 90, "Year": 365}
        length = ranges[st.segmented_control("Range", list(ranges), default="90 Days", key="weight_range",
                                             label_visibility="collapsed", width="stretch") or "90 Days"]
        points = recent(weight, length)
        if len(points) > 1:
            factor = 1 / formatting.KILOGRAMS_PER_POUND if unit == "lb" else 1.0
            frame = pd.DataFrame({"date": [moment for moment, _ in points], "weight": [v * factor for _, v in points]})
            ios.chart(alt.Chart(frame).mark_line(color=ios.color("blue"), interpolate="monotone", point=alt.OverlayMarkDef(
                color=ios.color("blue"), size=12 if len(points) > 40 else 30)).encode(
                    x=alt.X("date:T", title=None), y=alt.Y("weight:Q", title=None, scale=alt.Scale(zero=False))))
        if body_fat:
            ios.show(ios.rows([ios.row("Body fat", value=f"{body_fat[-1][1]:.1%}")]))

if resting or hrv or vo2max:
    with ios.card("heart", title="Heart & Fitness"):
        items = []
        if resting:
            ios.show(ios.rows([ios.row("Resting heart rate", value=f"{resting[-1][1]:.0f} bpm")]))
            month = recent(resting, 30)
            if len(month) > 1:
                frame = pd.DataFrame({"date": [moment for moment, _ in month], "bpm": [v for _, v in month]})
                ios.chart(alt.Chart(frame).mark_line(color=ios.color("red"), interpolate="monotone").encode(
                    x=alt.X("date:T", title=None), y=alt.Y("bpm:Q", title=None, scale=alt.Scale(zero=False))), height=120)
                items.append(ios.row("30-day average", value=f"{average(month):.0f} bpm"))
        if hrv:
            items.append(ios.row("HRV, 7-day average", value=f"{average(recent(hrv, 7)):.0f} ms"))
        if vo2max:
            items.append(ios.row("VO₂ max", value=f"{vo2max[-1][1]:.1f}"))
        if items:
            ios.show(ios.rows(items))

if sleep:
    week = recent(sleep, 7)
    with ios.card("sleep", title="Sleep"):
        ios.show(ios.rows([ios.row(day_name(sleep[-1][0], "Last night", "Night ending "),
                                   value=formatting.hours(sleep[-1][1]))]))
        ios.chart(day_bars(week, "indigo"), height=140)
        ios.show(ios.rows([ios.row("7-night average", value=formatting.hours(average(week)))]))

if steps:
    week = recent(steps, 7)
    with ios.card("activity", title="Activity"):
        ios.show(ios.rows([ios.row(day_name(steps[-1][0], "Steps today", "Steps on "), value=f"{steps[-1][1]:,.0f}")]))
        ios.chart(day_bars(week, "green"), height=140)
        items = [ios.row("7-day average", value=f"{average(week):,.0f}")]
        if energy:
            items.append(ios.row(day_name(energy[-1][0], "Active calories today", "Active calories on "),
                                 value=f"{energy[-1][1]:.0f} kcal"))
        ios.show(ios.rows(items))

imported = db.get_setting(conn, "apple_health_import")
if imported:
    when = datetime.fromtimestamp(imported["at"])
    ios.show(ios.footer(f"From Apple Health, imported {when:%b} {when.day}: {', '.join(imported['sources'])}. "
                        "Export again from the Health app for newer data."))

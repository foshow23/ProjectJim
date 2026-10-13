"""Turning numbers into the text shown on screen."""

from __future__ import annotations

from datetime import datetime

KILOGRAMS_PER_POUND = 0.45359237


def distance(meters: float | None) -> str:
    return f"{meters / 1000:.2f} km" if meters else "–"


def duration(seconds: float | None) -> str:
    """ "30:00" or "2:31:01"."""
    if not seconds:
        return "–"
    seconds = int(round(seconds))
    hours, minutes, secs = seconds // 3600, seconds % 3600 // 60, seconds % 60
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def pace(meters: float | None, seconds: float | None) -> str:
    """Minutes per kilometer, e.g. "5:24 /km"."""
    if not meters or not seconds:
        return "–"
    per_km = int(round(seconds / meters * 1000))
    return f"{per_km // 60}:{per_km % 60:02d} /km"


def weight(kilograms: float | None, unit: str = "kg", grouped: bool = False) -> str:
    """`unit` is "lb" or "kg". `grouped` rounds to a whole number with thousands separators,
    for big totals like a workout's volume ("10,756 lb")."""
    if kilograms is None:
        return "–"
    value = kilograms / KILOGRAMS_PER_POUND if unit == "lb" else kilograms
    if grouped:
        return f"{value:,.0f} {unit}"
    return f"{value:.1f}".rstrip("0").rstrip(".") + f" {unit}"


def number(value: float | None, suffix: str = "", digits: int = 0) -> str:
    return "–" if value is None else f"{value:,.{digits}f}{suffix}"


def hours(value: float | None) -> str:
    if value is None:
        return "–"
    minutes = int(round(value * 60))
    return f"{minutes // 60}h {minutes % 60}m"


def when(timestamp: float, with_time: bool = False) -> str:
    moment = datetime.fromtimestamp(timestamp)
    text = moment.strftime("%a %b %-d, %Y")
    return f"{text} at {moment.strftime('%-I:%M %p')}" if with_time else text


def short_date(timestamp: float) -> str:
    """ "Oct 6" this year, "Oct 6, 2025" for earlier years."""
    moment = datetime.fromtimestamp(timestamp)
    text = f"{moment:%b} {moment.day}"
    return text if moment.year == datetime.now().year else f"{text}, {moment.year}"

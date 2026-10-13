"""Lose It!'s data export (a zip): the food log, custom foods and calorie budget.

On loseit.com: account settings -> export your data.

Log entries have no ID, so a re-import replaces every Lose It! entry for the dates the export
covers. The export is the full log, so that's exact: edits and deletions in Lose It! carry
over, and importing the same export twice changes nothing.
"""

from __future__ import annotations

import csv
import io
import sqlite3
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime

from .. import db
from ..foods import Food, food_from_amount

SOURCE = "Lose It!"
NUTRIENT_COLUMNS = {"calories": "Calories", "protein": "Protein (g)", "carbs": "Carbohydrates (g)", "fat": "Fat (g)",
                    "saturated_fat": "Saturated Fat (g)", "sugars": "Sugars (g)", "fiber": "Fiber (g)",
                    "cholesterol": "Cholesterol (mg)", "sodium": "Sodium (mg)"}


class NotLoseItExport(ValueError):
    def __init__(self):
        super().__init__("That zip isn't a Lose It! export. On loseit.com, open your account settings and "
                         "export your data.")


@dataclass
class LoseItResult:
    entries: int = 0
    days: int = 0
    first_day: str | None = None
    last_day: str | None = None
    custom_foods: int = 0
    calorie_budget: float | None = None     # Lose It!'s budget on the most recent day


def is_loseit_export(archive: zipfile.ZipFile) -> bool:
    return any(name.rsplit("/", 1)[-1] == "food-logs.csv" for name in archive.namelist())


def number(text: str | None) -> float | None:
    """Lose It! writes large numbers with thousands separators ("1,160") and unknowns as "n/a"."""
    try:
        return float((text or "").replace(",", "").strip())
    except ValueError:
        return None


def parse_day(text: str | None) -> str | None:
    """MM/DD/YYYY (the local calendar day) -> YYYY-MM-DD."""
    try:
        return datetime.strptime((text or "").strip(), "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _rows(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    return [{(key or "").strip(): value for key, value in row.items()} for row in reader]


def parse_food_log(text: str) -> tuple[list[dict], tuple[str, str] | None]:
    """Returns the live entries and the (first, last) day in the file.

    Deleted rows (Lose It! keeps them, flagged) still count for the range: their days must be
    cleared too.
    """
    rows = _rows(text)
    if rows and not {"Date", "Name", "Calories"} <= set(rows[0]):
        raise NotLoseItExport()
    entries, days = [], []
    for row in rows:
        day = parse_day(row.get("Date"))
        if day is None:
            continue
        days.append(day)
        name = (row.get("Name") or "").strip()
        if (row.get("Deleted") or "0").strip() != "0" or not name:
            continue
        entry = {"day": day, "meal": (row.get("Meal") or "").strip() or "Other", "name": name,
                 "quantity": number(row.get("Quantity")) or 1.0, "units": (row.get("Units") or "").strip()}
        entry.update({key: number(row.get(column)) for key, column in NUTRIENT_COLUMNS.items()})
        entry["calories"] = entry["calories"] or 0.0
        entries.append(entry)
    return entries, ((min(days), max(days)) if days else None)


def parse_custom_foods(text: str) -> list[Food]:
    """custom-foods.csv: Name, Brand, Quantity, Measure, then nutrition for that amount."""
    foods = []
    for row in _rows(text):
        name = (row.get("Name") or "").strip()
        nutrients = {key: number(row.get(column)) for key, column in NUTRIENT_COLUMNS.items()}
        if not name or nutrients["calories"] is None:
            continue
        food = food_from_amount(
            id=f"loseit-custom:{(row.get('UniqueId') or name.lower()).strip()}", name=name,
            brand=(row.get("Brand") or "").strip() or None, quantity=number(row.get("Quantity")) or 1.0,
            units=(row.get("Measure") or "Serving"), nutrients=nutrients,
        )
        if food:
            foods.append(food)
    return foods


def latest_budget(text: str) -> float | None:
    """The most recent day's "Budget cals" from daily-calorie-summary.csv."""
    budgets = [(parse_day(row.get("Date")), number(row.get("Budget cals"))) for row in _rows(text)]
    budgets = [(day, budget) for day, budget in budgets if day and budget and budget > 0]
    return max(budgets)[1] if budgets else None


def import_archive(conn: sqlite3.Connection, archive: zipfile.ZipFile) -> LoseItResult:
    def read(file_name: str) -> str | None:
        member = next((n for n in archive.namelist() if n.rsplit("/", 1)[-1] == file_name), None)
        return archive.read(member).decode("utf-8-sig") if member else None

    log = read("food-logs.csv")
    if log is None:
        raise NotLoseItExport()
    entries, day_range = parse_food_log(log)
    result = LoseItResult(entries=len(entries), days=len({e["day"] for e in entries}))

    if day_range:
        result.first_day, result.last_day = day_range
        conn.execute("DELETE FROM food_entries WHERE source=? AND day BETWEEN ? AND ?", (SOURCE, *day_range))
    conn.executemany(
        "INSERT INTO food_entries (day, meal, name, quantity, units, calories, protein, carbs, fat, saturated_fat,"
        " sugars, fiber, cholesterol, sodium, source) VALUES (:day, :meal, :name, :quantity, :units, :calories,"
        f" :protein, :carbs, :fat, :saturated_fat, :sugars, :fiber, :cholesterol, :sodium, '{SOURCE}')",
        entries,
    )

    if (custom := read("custom-foods.csv")) is not None:
        foods = parse_custom_foods(custom)
        # The export lists every custom food, so replace the previous import's.
        conn.execute("DELETE FROM saved_foods WHERE source=?", (SOURCE,))
        conn.executemany("INSERT OR REPLACE INTO saved_foods (food_id, food_json, source, updated_at) VALUES (?, ?, ?, ?)",
                         [(food.id, food.to_json(), SOURCE, time.time()) for food in foods])
        result.custom_foods = len(foods)

    if (summary := read("daily-calorie-summary.csv")) is not None:
        result.calorie_budget = latest_budget(summary)
        # Use Lose It!'s budget as the calorie goal, unless one was already set in ProjectJim.
        if result.calorie_budget and db.get_setting(conn, "calorie_goal") is None:
            db.set_setting(conn, "calorie_goal", round(result.calorie_budget))
    conn.commit()
    return result

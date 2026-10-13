"""Nutrition: meal sections, each day's totals, logging foods, and your personal food list."""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import db
from .foods import Food, food_from_amount

LOGGED_HERE = "ProjectJim"
LOSE_IT = "Lose It!"

# --- Meal sections ----------------------------------------------------------------------------

DEFAULT_SECTIONS = ["Breakfast", "Lunch", "Afternoon Snack", "Dinner", "Post-Dinner Snack"]


def sections(conn: sqlite3.Connection) -> list[str]:
    return db.get_setting(conn, "meal_sections") or list(DEFAULT_SECTIONS)


def save_sections(conn: sqlite3.Connection, names: list[str], renamed: dict[str, str] | None = None) -> list[str]:
    """Saves the sections (blank and duplicate names dropped). `renamed` maps old -> new names;
    foods logged in ProjectJim follow their section's new name."""
    seen, cleaned = set(), []
    for name in (n.strip() for n in names):
        if name and name.lower() not in seen:
            seen.add(name.lower())
            cleaned.append(name)
    if not cleaned:
        cleaned = list(DEFAULT_SECTIONS)
    for old, new in (renamed or {}).items():
        if new.strip() and new != old:
            conn.execute("UPDATE food_entries SET meal=? WHERE source=? AND meal=?", (new.strip(), LOGGED_HERE, old))
    db.set_setting(conn, "meal_sections", cleaned)
    return cleaned


def _slot(meal: str) -> int | None:
    """Positions in the default layout: breakfast 0, lunch 1, afternoon snack 2, dinner 3, after dinner 4."""
    if "breakfast" in meal or "morning" in meal:
        return 0
    if "lunch" in meal:
        return 1
    if "afternoon" in meal:
        return 2
    if any(word in meal for word in ("post-dinner", "after dinner", "evening", "midnight", "late", "night")):
        return 4
    if "dinner" in meal or "supper" in meal:
        return 3
    if "snack" in meal:
        return 4
    return None


def section_index(meal: str, names: list[str]) -> int | None:
    """Which section a logged meal belongs in, or None if none fits (it then gets its own).

    Imported meal names (Lose It!'s "Afternoon Snacks", "Midnight Eats") map by position, so
    this still works after sections are renamed.
    """
    lowered = meal.strip().lower()
    for index, name in enumerate(names):
        if name.lower() == lowered:
            return index
    slot = _slot(lowered)
    return slot if slot is not None and slot < len(names) else None


def default_section_index(count: int, now: datetime | None = None) -> int:
    """The section to add to at this time of day."""
    hour = (now or datetime.now()).hour
    slot = 0 if 4 <= hour < 11 else 1 if hour < 15 and hour >= 11 else 2 if 15 <= hour < 17 else 3 if 17 <= hour < 21 else 4
    return min(slot, max(count - 1, 0))


# --- Goals ------------------------------------------------------------------------------------

GOAL_DEFAULTS = {"calories": 2200.0, "protein": 150.0, "carbs": 250.0, "fat": 70.0, "water": 2.5}
# (lowest, highest, step) offered when editing each goal.
GOAL_RANGES = {"calories": (1000.0, 5000.0, 50.0), "protein": (20.0, 400.0, 5.0), "carbs": (20.0, 600.0, 5.0),
               "fat": (20.0, 250.0, 5.0), "water": (0.5, 6.0, 0.25)}


def goals(conn: sqlite3.Connection) -> dict[str, float]:
    """Daily targets: kcal, grams of protein, carbs and fat, liters of water. The calorie goal
    starts as your Lose It! budget if you imported one."""
    saved = {"calories": db.get_setting(conn, "calorie_goal")} | (db.get_setting(conn, "macro_goals") or {})
    return {name: float(saved.get(name) or default) for name, default in GOAL_DEFAULTS.items()}


def save_goals(conn: sqlite3.Connection, values: dict[str, float]) -> None:
    db.set_setting(conn, "calorie_goal", values["calories"])
    db.set_setting(conn, "macro_goals", {name: values[name] for name in ("protein", "carbs", "fat", "water")})


# --- Days -------------------------------------------------------------------------------------

@dataclass
class Day:
    day: str                    # YYYY-MM-DD
    calories: float = 0.0
    protein: float = 0.0
    carbs: float = 0.0
    fat: float = 0.0
    water: float = 0.0          # liters (only Apple Health has it)
    fiber: float | None = None  # these four are only known from a food log
    sugars: float | None = None
    sodium: float | None = None
    saturated_fat: float | None = None
    source: str = ""            # "Lose It! food log", "Lose It! via Apple Health"
    entries: list[sqlite3.Row] = field(default_factory=list)

    @property
    def is_logged(self) -> bool:
        return self.calories > 0


def days(conn: sqlite3.Connection, last: date, count: int) -> list[Day]:
    """The `count` days ending with `last`, oldest first.

    A day with food-log entries uses those, since they carry every food and nutrient; other days
    use Apple Health's daily totals. Lose It! writes the same log to Health, so adding the two
    would double count.
    """
    first = last - timedelta(days=count - 1)
    span = (first.isoformat(), last.isoformat())
    entries: dict[str, list[sqlite3.Row]] = {}
    for row in conn.execute("SELECT * FROM food_entries WHERE day BETWEEN ? AND ? ORDER BY id", span):
        entries.setdefault(row["day"], []).append(row)
    health = {row["day"]: row for row in conn.execute("SELECT * FROM daily_nutrition WHERE day BETWEEN ? AND ?", span)}

    result = []
    for offset in range(count):
        key = (first + timedelta(days=offset)).isoformat()
        day = Day(day=key, water=health[key]["water"] if key in health else 0.0)
        rows = entries.get(key)
        if rows:
            def total(column, optional=False):
                values = [row[column] for row in rows if row[column] is not None]
                return None if optional and not values else sum(values)
            day.entries = rows
            day.calories, day.protein, day.carbs, day.fat = (total(c) for c in ("calories", "protein", "carbs", "fat"))
            day.fiber, day.sugars = total("fiber", True), total("sugars", True)
            day.sodium, day.saturated_fat = total("sodium", True), total("saturated_fat", True)
            day.source = ", ".join(sorted({row["source"] for row in rows})) + " food log"
        elif key in health:
            row = health[key]
            day.calories, day.protein, day.carbs, day.fat = row["calories"], row["protein"], row["carbs"], row["fat"]
            day.source = f"{row['source_names']} via Apple Health" if row["source_names"] else "Apple Health"
        result.append(day)
    return result


def meals(day: Day, names: list[str]) -> list[tuple[str, list[sqlite3.Row]]]:
    """Every meal section (empty ones too, so there's always somewhere to add), then any logged
    meal that doesn't fit a section."""
    grouped: list[tuple[str, list[sqlite3.Row]]] = [(name, []) for name in names]
    for entry in day.entries:
        index = section_index(entry["meal"], names)
        if index is None:
            index = next((i for i, (name, _) in enumerate(grouped) if name == entry["meal"]), None)
            if index is None:
                grouped.append((entry["meal"], []))
                index = len(grouped) - 1
        grouped[index][1].append(entry)
    return grouped


def last_logged_day(conn: sqlite3.Connection, before: date) -> date | None:
    row = conn.execute(
        "SELECT MAX(day) AS day FROM (SELECT day FROM food_entries WHERE day < :d"
        " UNION ALL SELECT day FROM daily_nutrition WHERE day < :d AND calories > 0)", {"d": before.isoformat()},
    ).fetchone()
    return date.fromisoformat(row["day"]) if row and row["day"] else None


def top_foods(conn: sqlite3.Connection, last: date, count: int = 30, limit: int = 5) -> list[sqlite3.Row]:
    """Foods that contributed the most calories over the `count` days ending with `last`."""
    first = last - timedelta(days=count - 1)
    return conn.execute(
        "SELECT name, COUNT(*) AS times, SUM(calories) AS calories, SUM(COALESCE(protein, 0)) AS protein"
        " FROM food_entries WHERE day BETWEEN ? AND ? GROUP BY name ORDER BY calories DESC LIMIT ?",
        (first.isoformat(), last.isoformat(), limit),
    ).fetchall()


# --- Logging ----------------------------------------------------------------------------------

def _entry_values(food: Food, amount: float, unit: str, meal: str) -> dict:
    grams = food.grams(amount, unit)
    values = food.nutrients(grams)
    values["calories"] = round(values["calories"])
    values.update(meal=meal, name=food.display_name, quantity=amount, units=unit, food_json=food.to_json(),
                  grams=None if food.portion_only else grams)
    return values


def add_entry(conn: sqlite3.Connection, food: Food, amount: float, unit: str, meal: str, day: date) -> int:
    values = _entry_values(food, amount, unit, meal) | {"day": day.isoformat(), "source": LOGGED_HERE}
    cursor = conn.execute(
        "INSERT INTO food_entries (day, meal, name, quantity, units, calories, protein, carbs, fat, saturated_fat,"
        " sugars, fiber, cholesterol, sodium, source, food_json, grams) VALUES (:day, :meal, :name, :quantity,"
        " :units, :calories, :protein, :carbs, :fat, :saturated_fat, :sugars, :fiber, :cholesterol, :sodium,"
        " :source, :food_json, :grams)", values)
    conn.commit()
    return cursor.lastrowid


def update_entry(conn: sqlite3.Connection, entry_id: int, food: Food, amount: float, unit: str, meal: str) -> None:
    values = _entry_values(food, amount, unit, meal) | {"id": entry_id}
    conn.execute(
        "UPDATE food_entries SET meal=:meal, name=:name, quantity=:quantity, units=:units, calories=:calories,"
        " protein=:protein, carbs=:carbs, fat=:fat, saturated_fat=:saturated_fat, sugars=:sugars, fiber=:fiber,"
        " cholesterol=:cholesterol, sodium=:sodium, food_json=:food_json, grams=:grams WHERE id=:id", values)
    conn.commit()


def delete_entry(conn: sqlite3.Connection, entry_id: int) -> None:
    conn.execute("DELETE FROM food_entries WHERE id=?", (entry_id,))
    conn.commit()


def save_food(conn: sqlite3.Connection, food: Food, source: str = LOGGED_HERE) -> None:
    """Adds a food to your library."""
    conn.execute("INSERT OR REPLACE INTO saved_foods (food_id, food_json, source, updated_at) VALUES (?, ?, ?, ?)",
                 (food.id, food.to_json(), source, time.time()))
    conn.commit()


# --- Your foods -------------------------------------------------------------------------------

def food_from_entry(entry: sqlite3.Row) -> Food | None:
    """The food behind a log entry: the stored database food, or one rebuilt from a Lose It! entry."""
    if entry["food_json"]:
        return Food.from_json(entry["food_json"])
    if entry["source"] != LOSE_IT:
        return None
    keys = ("calories", "protein", "carbs", "fat", "saturated_fat", "sugars", "fiber", "cholesterol", "sodium")
    return food_from_amount(id=f"loseit:{entry['name'].lower()}", name=entry["name"], quantity=entry["quantity"],
                            units=entry["units"], nutrients={key: entry[key] for key in keys})


def personal_foods(conn: sqlite3.Connection) -> list[tuple[Food, int]]:
    """Everything you've eaten or saved, with how often, most-eaten first: foods you created,
    your Lose It! custom foods, and every food you've logged. Works offline.

    Keyed by name, so a custom food and the log entries made from it are one food.
    """
    foods: dict[str, list] = {}
    for row in conn.execute("SELECT food_json FROM saved_foods ORDER BY updated_at DESC"):
        if (food := Food.from_json(row["food_json"])) and food.name.lower() not in foods:
            foods[food.name.lower()] = [food, 0]
    # Newest first; a name not in your library keeps its most recent nutrition.
    for entry in conn.execute("SELECT * FROM food_entries ORDER BY day DESC, id DESC"):
        key = entry["name"].lower()
        if entry["food_json"] and (stored := Food.from_json(entry["food_json"])):
            key = stored.name.lower()
        if key not in foods:
            food = food_from_entry(entry)
            if food is None:
                continue
            foods[key] = [food, 0]
        foods[key][1] += 1
    return sorted(((food, count) for food, count in foods.values()), key=lambda pair: (-pair[1], pair[0].name))


def recent_foods(conn: sqlite3.Connection, limit: int = 15) -> list[Food]:
    """Distinct foods logged in ProjectJim, most recent first."""
    foods: dict[str, Food] = {}
    for entry in conn.execute("SELECT food_json FROM food_entries WHERE source=? ORDER BY day DESC, id DESC",
                              (LOGGED_HERE,)):
        food = Food.from_json(entry["food_json"])
        if food and food.id not in foods:
            foods[food.id] = food
            if len(foods) == limit:
                break
    return list(foods.values())


def matching(query: str, foods: list[tuple[Food, int]]) -> list[tuple[Food, int]]:
    """Foods whose name has a word starting with every word of the query."""
    terms = re.findall(r"[a-z0-9]+", query.lower())
    if not terms:
        return []
    result = []
    for food, count in foods:
        words = re.findall(r"[a-z0-9]+", food.display_name.lower())
        if all(any(word.startswith(term) for word in words) for term in terms):
            result.append((food, count))
    return result

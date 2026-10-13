"""Meal sections, daily totals, logging foods and your personal food list."""

from datetime import date, datetime

import pytest
from conftest import count

from jim import nutrition
from jim.foods import Food, Portion

DAY = date(2026, 10, 10)


def rice() -> Food:
    return Food(id="usda:1", name="Jollof Rice", source="usda", calories=160.0, protein=3.0, carbs=30.0, fat=3.0,
                sodium=300.0, portions=[Portion("cup", 200)])


def test_five_meal_sections_by_default_and_they_can_be_edited(conn):
    assert nutrition.sections(conn) == ["Breakfast", "Lunch", "Afternoon Snack", "Dinner", "Post-Dinner Snack"]
    entry = nutrition.add_entry(conn, rice(), 1, "cup", "Dinner", DAY)
    saved = nutrition.save_sections(conn, ["Breakfast", "Lunch", " ", "Supper", "lunch", "Dessert"], {"Dinner": "Supper"})
    assert saved == ["Breakfast", "Lunch", "Supper", "Dessert"]     # blanks and repeats dropped
    assert nutrition.sections(conn) == saved
    # The food logged under Dinner followed the rename.
    assert conn.execute("SELECT meal FROM food_entries WHERE id=?", (entry,)).fetchone()[0] == "Supper"
    assert nutrition.save_sections(conn, ["", " "]) == nutrition.DEFAULT_SECTIONS


def test_imported_meal_names_map_to_sections_by_position():
    names = ["Morning", "Midday", "Snack", "Evening", "Late"]      # renamed sections
    assert nutrition.section_index("Breakfast", names) == 0
    assert nutrition.section_index("Afternoon Snacks", names) == 2
    assert nutrition.section_index("Dinner", names) == 3
    assert nutrition.section_index("Midnight Eats", names) == 4
    assert nutrition.section_index("Late", names) == 4             # an exact name wins
    assert nutrition.section_index("Brunch", names) is None
    assert nutrition.section_index("Dinner", ["One", "Two"]) is None    # that position was removed


def test_default_section_follows_the_time_of_day():
    at = lambda hour: nutrition.default_section_index(5, datetime(2026, 10, 10, hour))
    assert [at(8), at(12), at(16), at(19), at(23), at(2)] == [0, 1, 2, 3, 4, 4]
    assert nutrition.default_section_index(2, datetime(2026, 10, 10, 19)) == 1      # fewer sections than slots


def test_logging_editing_and_deleting_a_food(conn):
    entry = nutrition.add_entry(conn, rice(), 1.5, "cup", "Lunch", DAY)
    row = conn.execute("SELECT * FROM food_entries WHERE id=?", (entry,)).fetchone()
    assert (row["calories"], row["protein"], row["grams"], row["units"], row["source"]) == (480, 9.0, 300, "cup", "ProjectJim")

    nutrition.update_entry(conn, entry, rice(), 100, "g", "Dinner")
    row = conn.execute("SELECT * FROM food_entries WHERE id=?", (entry,)).fetchone()
    assert (row["calories"], row["meal"], row["quantity"], row["units"]) == (160, "Dinner", 100, "g")
    assert nutrition.food_from_entry(row) == rice()

    nutrition.delete_entry(conn, entry)
    assert count(conn, "food_entries") == 0


def test_a_day_totals_its_entries_and_groups_them_by_meal(conn):
    nutrition.add_entry(conn, rice(), 1, "cup", "Lunch", DAY)
    nutrition.add_entry(conn, rice(), 0.5, "cup", "Brunch", DAY)
    day = nutrition.days(conn, DAY, 1)[0]
    assert (day.calories, day.protein, day.sodium, day.fiber) == (480, 9.0, 900.0, None)
    assert day.is_logged and day.source == "ProjectJim food log"
    meals = nutrition.meals(day, nutrition.sections(conn))
    assert [(name, len(entries)) for name, entries in meals] == [
        ("Breakfast", 0), ("Lunch", 1), ("Afternoon Snack", 0), ("Dinner", 0), ("Post-Dinner Snack", 0), ("Brunch", 1)]

    empty = nutrition.days(conn, date(2026, 10, 12), 3)
    assert [d.day for d in empty] == ["2026-10-10", "2026-10-11", "2026-10-12"]
    assert not empty[-1].is_logged
    assert nutrition.last_logged_day(conn, date(2026, 10, 12)) == DAY
    assert nutrition.last_logged_day(conn, DAY) is None


def test_your_foods_are_ranked_by_how_often_you_eat_them(conn):
    for _ in range(3):
        nutrition.add_entry(conn, rice(), 1, "cup", "Lunch", DAY)
    conn.execute("INSERT INTO food_entries (day, meal, name, quantity, units, calories, protein, source)"
                 " VALUES ('2026-10-09', 'Breakfast', 'Agege Bread', 2, 'Slices', 140, 5, 'Lose It!')")
    created = Food(id="custom:1", name="Ewa Riro", source="custom", calories=120.0)
    nutrition.save_food(conn, created)

    foods = nutrition.personal_foods(conn)
    assert [(food.name, times) for food, times in foods] == [("Jollof Rice", 3), ("Agege Bread", 1), ("Ewa Riro", 0)]
    bread = foods[1][0]
    assert bread.unit_labels() == ["slice"] and bread.nutrients(bread.grams(1, "slice"))["calories"] == pytest.approx(70)

    assert [food.name for food, _ in nutrition.matching("jol ri", foods)] == ["Jollof Rice"]     # word prefixes
    assert [food.name for food, _ in nutrition.matching("bread", foods)] == ["Agege Bread"]
    assert nutrition.matching("ice", foods) == [] and nutrition.matching("  ", foods) == []
    assert [food.name for food in nutrition.recent_foods(conn)] == ["Jollof Rice"]


def test_top_foods_by_calories(conn):
    nutrition.add_entry(conn, rice(), 2, "cup", "Lunch", DAY)
    nutrition.add_entry(conn, rice(), 1, "cup", "Dinner", DAY)
    top = nutrition.top_foods(conn, DAY, 30)
    assert [(row["name"], row["times"], row["calories"]) for row in top] == [("Jollof Rice", 2, 960)]
    assert nutrition.top_foods(conn, date(2026, 9, 1), 5) == []


def test_goals(conn):
    assert nutrition.goals(conn) == nutrition.GOAL_DEFAULTS
    nutrition.save_goals(conn, {"calories": 2400, "protein": 180, "carbs": 260, "fat": 80, "water": 3})
    assert nutrition.goals(conn) == {"calories": 2400, "protein": 180, "carbs": 260, "fat": 80, "water": 3}

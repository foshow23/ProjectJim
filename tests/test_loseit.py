"""Lose It!'s data export."""

import io
import zipfile

import pytest
from conftest import count

from jim import db, nutrition
from jim.importers import loseit

LOG_HEADER = ("Date,Name,Icon,Meal,Quantity,Units,Calories,Deleted,Fat (g),Protein (g),Carbohydrates (g),"
              "Saturated Fat (g),Sugars (g),Fiber (g),Cholesterol (mg),Sodium (mg)\n")
LOG = (LOG_HEADER
       + '10/01/2026,Agege Bread,Bread,Breakfast,2,Slices,140,0,2,5,27,1,4,2,0,150\n'
       + '10/01/2026,"Rice, Jollof",Rice,Dinner,1.5,Cups,"1,160",0,30,20,190,n/a,n/a,6,n/a,"1,900"\n'
       + '10/01/2026,Removed Snack,Snack,Snacks,1,Each,300,1,10,2,40,3,20,1,0,90\n'
       + '10/02/2026,Honey,Honey,Breakfast,1,Tablespoon,60,0,0,0,17,0,16,0,0,1\n')
CUSTOM = ("Name,Brand,Quantity,Measure,Calories,Fat (g),Protein (g),Carbohydrates (g),UniqueId\n"
          "Ewa Riro,Home,1,Serving,320,9,18,42,abc123\n"
          "Zobo,,250,Milliliters,90,0,0,22,def456\n")
SUMMARY = "Date,Food cals,Exercise cals,Budget cals\n09/30/2026,1900,0,2200\n10/02/2026,2100,300,2150\n"


def export(log=LOG, custom=CUSTOM, summary=SUMMARY) -> zipfile.ZipFile:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("food-logs.csv", log)
        archive.writestr("custom-foods.csv", custom)
        archive.writestr("daily-calorie-summary.csv", summary)
    return zipfile.ZipFile(data)


def test_numbers_with_thousands_separators_and_unknowns():
    assert loseit.number("1,160") == 1160
    assert loseit.number("n/a") is None
    assert loseit.parse_day("10/02/2026") == "2026-10-02"


def test_imports_the_log_skipping_deleted_rows(conn):
    result = loseit.import_archive(conn, export())
    assert (result.entries, result.days, result.first_day, result.last_day) == (3, 2, "2026-10-01", "2026-10-02")
    jollof = conn.execute("SELECT * FROM food_entries WHERE name='Rice, Jollof'").fetchone()
    assert (jollof["calories"], jollof["sodium"], jollof["sugars"], jollof["quantity"]) == (1160, 1900, None, 1.5)
    assert count(conn, "food_entries") == 3


def test_a_newer_export_replaces_the_days_it_covers(conn):
    loseit.import_archive(conn, export())
    # An entry logged in ProjectJim on a covered day must survive.
    conn.execute("INSERT INTO food_entries (day, meal, name, quantity, units, calories, source)"
                 " VALUES ('2026-10-01', 'Lunch', 'Suya', 1, 'serving', 400, 'ProjectJim')")
    newer = LOG_HEADER + '10/01/2026,Agege Bread,Bread,Breakfast,3,Slices,210,0,3,7,40,1,6,3,0,225\n'
    loseit.import_archive(conn, export(log=newer))
    rows = conn.execute("SELECT name, calories FROM food_entries WHERE day='2026-10-01' ORDER BY name").fetchall()
    assert [(row["name"], row["calories"]) for row in rows] == [("Agege Bread", 210), ("Suya", 400)]
    # Oct 2 wasn't in the newer file's range, so it's untouched.
    assert conn.execute("SELECT COUNT(*) FROM food_entries WHERE day='2026-10-02'").fetchone()[0] == 1


def test_custom_foods_and_the_calorie_budget(conn):
    result = loseit.import_archive(conn, export())
    assert (result.custom_foods, result.calorie_budget) == (2, 2150)
    assert nutrition.goals(conn)["calories"] == 2150
    foods = {food.name: food for food, _ in nutrition.personal_foods(conn)}
    assert foods["Ewa Riro"].portion_only and foods["Ewa Riro"].brand == "Home"
    assert foods["Zobo"].grams(250, "ml") == pytest.approx(250)
    assert foods["Zobo"].nutrients(250)["calories"] == pytest.approx(90)

    # A goal set in ProjectJim isn't overwritten by a later import.
    db.set_setting(conn, "calorie_goal", 2400)
    loseit.import_archive(conn, export())
    assert nutrition.goals(conn)["calories"] == 2400


def test_rejects_other_zips(conn):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("notes.txt", "hello")
    assert not loseit.is_loseit_export(zipfile.ZipFile(data))
    with pytest.raises(loseit.NotLoseItExport):
        loseit.import_archive(conn, zipfile.ZipFile(data))

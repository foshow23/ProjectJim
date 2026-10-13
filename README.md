# ProjectJim

A personal fitness app that brings my training, food and body data together in one place.
Garmin records my runs and rides, Hevy my lifting, Lose It! my meals, and Apple Health a bit of
everything, so the same workout often exists in three apps. ProjectJim imports from each,
works out which records are the same real session, and keeps **one** copy of everything in its
own database.

It's a Python web app (Streamlit) styled to look and feel like an iPhone app:
a tab bar at the bottom, grouped lists, tap a workout to open it, sheets for adding food.

## What it does

| Tab | What's there |
| --- | --- |
| **Cardio** | Runs, rides, walks, hikes and swims. Each opens to a GPS route map, stats and a heart-rate chart. |
| **Strength** | Hevy workouts with every set, plus heart rate and calories from the watch when it recorded the same session. |
| **Nutrition** | A food diary in five meal sections you can rename and reorder, totals against daily goals, trends and top foods. |
| **Body** | Weight, resting heart rate, HRV, VO₂ max, sleep and steps from Apple Health. |
| **Insights** | The shell for an AI coach (this week's numbers, and a question box that isn't switched on yet). |

Adding food:

- **Your Foods**: everything you've logged before, most-eaten first. Works offline.
- **USDA FoodData Central** (whole foods and dishes) and **Open Food Facts** (packaged products), searched online.
- **Barcode** lookup, or **create** a food of your own.
- **AI estimate**: describe a meal or add a photo; Claude asks a quick question or two (how oily? how big?) with tap-to-answer choices, then estimates the calories and macros.
- Any unit: g, kg, mg, oz, lb, ml, L, tsp, tbsp, fl oz, cup, pint, quart, plus the food's own portions (slice, serving). Volume converts to weight using the food's density, so a tablespoon of honey counts as 21 g, not 15.

## One workout per real session

Every source's version of a workout is kept as its own record, and records are grouped into
workouts by these rules ([jim/merger.py](jim/merger.py)):

1. Same source and same ID: it's the same record, so it's updated. Importing a file twice never adds copies.
2. Different sources: the same session when the activity types are compatible, the times overlap by at least half of the shorter one, and the distances (if both have one) are within 10%.
3. Two records from the same app are never merged, so back-to-back sessions stay separate.

The merged workout takes timing, heart rate and calories from the watch (Garmin file, then
Apple Health) and its name and sets from where they were typed (Hevy, then Garmin). A workout's
detail lists its sources at the bottom. Deleting a workout in Hevy or Garmin removes it here on
the next import, and changing a start time in Garmin doesn't create a duplicate.

Food works the same way: a day with a food log ignores Apple Health's totals for that day, and
steps count one device per day, so nothing is added up twice.

## Run it

Built and tested with Python 3.13.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py
```

It opens at http://localhost:8501. Tap the ⟳ button at the top right of any tab to open
**Sources** and import your files:

| Source | What to import |
| --- | --- |
| Garmin | Your data export (.zip, requested from Garmin's website) for the full history, or single activities as .fit, .tcx or .gpx |
| Hevy | The workout export (.csv): Profile → Settings → Export & Import Data |
| Lose It! | The data export (.zip) from your account settings on loseit.com |
| Apple Health | export.zip: Health app → profile picture → Export All Health Data |

Everything is stored in `data/projectjim.db` on your computer. That folder and `.env` are
gitignored, so personal data and keys never reach GitHub.

### Keys (optional)

Copy `.env.example` to `.env` and fill in:

- `ANTHROPIC_API_KEY`: for AI estimates. Uses Claude Haiku 4.5, the most cost-efficient model.
- `USDA_API_KEY`: a free key from api.data.gov/signup. Without one, USDA's shared demo key allows only a few searches an hour.

### On your phone

By default only this computer can open the app. To use it from your phone on the same Wi-Fi:

```bash
.venv/bin/streamlit run app.py --server.address 0.0.0.0
```

Then open the "Network URL" it prints in Safari, and use Share → Add to Home Screen. While
it runs this way, anyone on that Wi-Fi could open it, so only do this on a network you trust.

## Tests

```bash
.venv/bin/python -m pytest
```

74 tests on made-up data: merging and duplicates, each importer, units and densities, the food
diary, food database parsing, and the AI estimator (with a stand-in for Claude, so no key or
network is needed).

## How the code is organized

```
app.py                 The frame: stylesheet, tab bar, which tab is showing
views/                 One file per tab
jim/
  db.py                The SQLite tables
  models.py            A workout as one source describes it; GPS routes
  merger.py            Groups records from different sources into one workout
  importers/           hevy, garmin_files, garmin_export, loseit, apple_health
  foods.py             Foods, units, densities
  nutrition.py         Meal sections, daily totals, logging, Your Foods
  food_search.py       USDA and Open Food Facts
  estimator.py         The AI macro estimator (Claude)
  ios.py               The iOS look: stylesheet and list/tab-bar builders
  workout_views.py     Workout rows and the detail screen
  food_sheets.py       Add Food, amount, create food, AI chat, goals, meals
  sources_sheet.py     The Sources sheet
tests/                 pytest
```

The iOS look is plain HTML and CSS built in Python ([jim/ios.py](jim/ios.py)), with one small
JavaScript component that reports which row was tapped. All text is escaped before it goes
into the page, since food names come from public databases and an AI.

## Credits

- **Icons**: [Material Symbols](https://fonts.google.com/icons) by Google, Apache License 2.0 (bundled with Streamlit). The app icon is the 🏋️ emoji.
- **Maps**: map tiles and data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, © [CARTO](https://carto.com/attributions); drawn with [deck.gl](https://deck.gl).
- **Food data**: [USDA FoodData Central](https://fdc.nal.usda.gov) (public domain) and [Open Food Facts](https://world.openfoodfacts.org) (Open Database License).
- **Weight of a cup or spoonful**: King Arthur Baking's [ingredient weight chart](https://www.kingarthurbaking.com/learn/ingredient-weight-chart).
- **AI estimates**: [Claude](https://www.anthropic.com/claude) by Anthropic.
- **Design**: follows Apple's iOS conventions and system colors. No Apple artwork or SF Symbols are used.

ProjectJim isn't affiliated with Garmin, Hevy, Lose It!, Apple or any service above.

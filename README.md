# ProjectJim

A personal fitness app that brings your training, food and body data together in one place. Import your Garmin, Hevy, Lose It! and Apple Health exports, and ProjectJim merges them into one clean history: one workout per real session, however many apps recorded it.

It's a Python web app (Streamlit) styled to look and feel like an iPhone app: a tab bar at the bottom, grouped lists, and sheets that slide up.

## Features

* **One workout per session**: Garmin, Hevy and Apple Health records of the same session are merged automatically. Re-importing never creates duplicates.
* **Cardio**: Runs, rides, walks, hikes and swims, each with a GPS route map, stats and a heart-rate chart
* **Strength**: Every Hevy set, plus heart rate and calories from your watch for the same session
* **Bodyweight Exercises**: Pull-ups, dips and push-ups count your body weight on the day, not 0 kg
* **Nutrition Diary**: Five meal sections you can rename and reorder, daily goals, trends and your most-eaten foods
* **Food Search**: Your own foods, USDA FoodData Central, Open Food Facts, barcodes, or a food you create
* **AI Meal Estimates**: Describe a meal or snap a photo; Claude asks a quick question or two, then estimates the calories and macros
* **Any Unit**: g, oz, lb, ml, cups, tablespoons and more. Volume converts by density, so a tablespoon of honey counts as 21 g, not 15
* **Body**: Weight, resting heart rate, HRV, VO₂ max, sleep and steps
* **AI Coach**: Ask questions about your own data ("Which lifts have stalled?"). Answers come only from ProjectJim, and every number is checked
* **Password Protection**: Optional sign-in screen, required before the app can be opened from other devices
* **iPhone Look**: Apple's system colors, large titles and grouped lists, in a phone-width layout

## Installation

1. **Get the code**:
   ```bash
   git clone https://github.com/foshow23/ProjectJim.git
   cd ProjectJim
   ```
2. **Install**: You'll need Python 3.13. Every library is pinned to the exact version it was tested with.
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements-lock.txt
   ```
3. **Add your keys (optional)**: Copy `.env.example` to `.env` and fill in what you need:
   * `ANTHROPIC_API_KEY`: for AI meal estimates and the coach ([console.anthropic.com](https://console.anthropic.com) → API Keys). Both use Claude Haiku 5.5, about a tenth of a cent per question.
   * `USDA_API_KEY`: a free key from [api.data.gov/signup](https://api.data.gov/signup). Without one, USDA's shared demo key allows only a few searches an hour.
   * `PROJECTJIM_PASSWORD`: a password asked for before the app opens.
4. **Run it**:
   ```bash
   .venv/bin/streamlit run app.py
   ```
   It opens at http://localhost:8501. In VS Code, use **Run and Debug → ProjectJim** (F5) instead of "Run Python File": a Streamlit app has to be started by Streamlit.

## How to Use

### Importing Your Data

Tap the **⟳** button at the top right of any tab to open **Sources**, then drop in your exports:

* **Garmin**: Your full data export (.zip, requested from Garmin's website), or single activities as .fit, .tcx or .gpx
* **Hevy**: The workout export (.csv): Profile → Settings → Export & Import Data
* **Lose It!**: The data export (.zip) from your account settings on loseit.com
* **Apple Health**: export.zip: Health app → your profile picture → Export All Health Data

Import again whenever you want fresh data. Existing records are updated, never duplicated, and workouts you deleted in Hevy or Garmin are removed.

### Logging Food

On **Nutrition**, tap **Add Food** under a meal and:

* Search your own foods (works offline), USDA or Open Food Facts
* Look up a barcode, or create a food of your own
* Tap **AI estimate** to describe a meal or add a photo, answer a quick question or two, and log the estimate

### Asking the Coach

On **Insights**, tap a suggested question or type your own. Follow-ups work ("what about last month?"). Under each answer is what the coach looked up, and any number it couldn't find in your data is flagged in orange.

### Using It on Your Phone

By default only your computer can open the app. To use it from your phone on the same Wi-Fi:

1. Set `PROJECTJIM_PASSWORD` in `.env`. The app won't open in this mode without one.
2. Start it with:
   ```bash
   .venv/bin/streamlit run app.py --server.address 0.0.0.0
   ```
3. Open the "Network URL" it prints in Safari, sign in, and use Share → Add to Home Screen.

Only do this on a network you trust: others on the Wi-Fi can reach the sign-in screen, and the connection isn't encrypted (plain http).

## Customization

* **Daily Goals**: Calories, protein, carbs and fat (Nutrition → the target button at the top)
* **Meal Sections**: Rename, reorder, add or remove them (Nutrition → Edit Meals)
* **Weight Unit**: Kilograms or pounds (Body → the scale button at the top)
* **AI Model**: Set `PROJECTJIM_MODEL` in `.env` (e.g. `claude-sonnet-5-5`) for more accuracy at a higher price
* **Light or Dark**: The app is always light; change `base` in `.streamlit/config.toml` to `"dark"` for the dark version

## How It Works

### One Workout per Session

Every source's version of a workout is kept as its own record, and records are grouped into workouts by these rules ([jim/merger.py](jim/merger.py)):

1. **Same source and ID**: It's the same record, so it's updated. Importing a file twice never adds copies.
2. **Different sources**: The same session when the activity types are compatible, the times overlap by at least half of the shorter one, and the distances (if both have one) are within 10%.
3. **Same app**: Two records from the same app are never merged, so back-to-back sessions stay separate.
4. **Apple Health only fills in**: Hevy's export has no heart rate or calories, so Health's copy of a session adds them. A Health workout that matches nothing from Hevy or Garmin is skipped: with no name or sets, it would only show up as a bare "Strength".

The merged workout takes timing, heart rate and calories from the watch (Garmin, then Apple Health), and its name and sets from where they were typed (Hevy, then Garmin). Each kind of data has one main source, so nothing is added up twice: workouts from Garmin and Hevy, food from Lose It! (or logged in ProjectJim), and body measurements from Apple Health. Within Health, steps and active calories count one device per day, so a watch and a phone aren't added together.

### The AI Coach Stays Inside Your Data

The coach ([jim/coach.py](jim/coach.py)) is kept to ProjectJim's data in three ways:

1. **Its only tools read this app's database**: workouts, one exercise's history, a body measurement, or the food log. It has no web search. The tools also work out totals, averages and changes, so it never does maths itself.
2. **Its instructions** tell it to answer only from that data, and to say "I don't have that in ProjectJim" (and which export would add it) when something's missing.
3. **Every number is checked**: Python looks for each number in the answer, allowing rounding, in what the AI was shown. If one isn't there, the AI rewrites the answer once; anything still unverified is flagged.

To keep it quick and cheap, every question comes with a summary of the last 4 weeks (so simple questions need no lookups), long lists are averaged by week or month before they're sent, and the coach asks for all its lookups at once.

### Bodyweight Exercises

Hevy logs a pull-up with no weight. ProjectJim counts your body weight from the Body tab instead (the closest weigh-in before the workout), plus a dip belt's weight or minus an assisted machine's help. A push-up counts 64% of body weight ([jim/bodyweight.py](jim/bodyweight.py)).

## Requirements

* Python 3.13
* A computer that runs Python, and a modern browser (built and tested on macOS)
* Your own exports from Garmin, Hevy, Lose It! and/or Apple Health (any combination works)
* An Anthropic API key for the AI features (optional; everything else works without one)

## Privacy & Security

* **Your Data Stays Local**: Everything is stored in `data/projectjim.db` on your computer. That folder and `.env` are gitignored, so personal data and keys never reach GitHub.
* **What Leaves Your Computer**: Coach questions send summaries of your data to Anthropic's API, and meal estimates send the photo. Food searches go to USDA and Open Food Facts. Nothing else is sent anywhere.
* **Local Only by Default**: The app only accepts connections from your own computer unless you start it in phone mode, which requires a password.
* **Safe Display**: All text is escaped before it's shown, since food names come from public databases and an AI.
* **Pinned Dependencies**: `requirements.txt` lists the libraries ProjectJim uses directly, and `requirements-lock.txt` everything they depend on, all at exact versions. They were checked with [pip-audit](https://pypi.org/project/pip-audit/) for known vulnerabilities.

## Running the Tests

```bash
.venv/bin/python -m pytest
```

107 tests on made-up data: merging and duplicates, each importer, units and densities, the food diary, food database parsing, bodyweight loads, the password screen, the AI estimator and the coach (with a stand-in for Claude, so no key or network is needed).

## Project Structure

```
app.py                 The frame: password check, stylesheet, tab bar, which tab is showing
views/                 One file per tab
jim/
  db.py                The SQLite tables
  models.py            A workout as one source describes it; GPS routes
  merger.py            Groups records from different sources into one workout
  importers/           hevy, garmin_files, garmin_export, loseit, apple_health
  foods.py             Foods, units, densities
  nutrition.py         Meal sections, daily totals, logging, Your Foods
  food_search.py       USDA and Open Food Facts
  estimator.py         The AI meal estimator (Claude)
  coach.py             The Insights coach: its lookups and the number check (Claude)
  bodyweight.py        How much a pull-up, dip or push-up set lifted
  login.py             The password screen
  ios.py               The iPhone look: stylesheet and list/tab-bar builders
  workout_views.py     Workout rows and the detail screen
  food_sheets.py       Add Food, amount, create food, AI chat, goals, meals
  sources_sheet.py     The Sources sheet
tests/                 pytest
```

The iPhone look is plain HTML and CSS built in Python ([jim/ios.py](jim/ios.py)), with one small JavaScript component that reports which row was tapped.

## Credits

* **Icons**: [Material Symbols](https://fonts.google.com/icons) by Google, Apache License 2.0 (bundled with Streamlit). The app icon is the 🏋️ emoji.
* **Maps**: Map tiles and data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, © [CARTO](https://carto.com/attributions); drawn with [deck.gl](https://deck.gl).
* **Food Data**: [USDA FoodData Central](https://fdc.nal.usda.gov) (public domain) and [Open Food Facts](https://world.openfoodfacts.org) (Open Database License).
* **Weight of a Cup or Spoonful**: King Arthur Baking's [ingredient weight chart](https://www.kingarthurbaking.com/learn/ingredient-weight-chart).
* **AI Estimates and the Coach**: [Claude](https://www.anthropic.com/claude) by Anthropic.
* **Push-up Load (64% of body weight)**: Ebben, W. P., Wurm, B., VanderZanden, T. L., et al. (2011). Kinetic analysis of several variations of push-ups. *Journal of Strength and Conditioning Research*, 25(10), 2891–2894.
* **Design**: Follows Apple's iOS conventions and system colors. No Apple artwork or SF Symbols are used.

ProjectJim isn't affiliated with Garmin, Hevy, Lose It!, Apple or any service above.

## License

[MIT](LICENSE): you're free to use, change and share the code, as long as the copyright notice stays with it. The credited data and icons above keep their own licenses.

## Feedback

Feedback and suggestions are welcome! Feel free to open an issue.

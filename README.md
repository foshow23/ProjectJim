# ProjectJim

I track my runs with a Garmin watch, my lifting in Hevy, my food in Lose It!, and my weight and sleep end up in Apple Health. None of those apps talk to each other, and the same workout often shows up in two or three of them. ProjectJim pulls all of it into one place, matches up the duplicates, and lets me see everything together.

It's a Python web app built with Streamlit, made to look like an iPhone app: tab bar at the bottom, grouped lists, sheets that slide up.

## What it does

- Imports exports from Garmin, Hevy, Lose It! and Apple Health, and merges records of the same workout into one
- Cardio tab with route maps, pace and heart rate for runs, rides, walks and hikes
- Strength tab with every set from Hevy, plus heart rate and calories from the watch
- Counts your body weight for pull-ups, dips and push-ups, since Hevy logs them as 0 kg
- A food diary with meal sections, daily goals and trends. Foods come from your history, USDA, Open Food Facts, barcodes, or ones you make yourself
- AI meal estimates: describe a meal or take a photo and Claude estimates the calories and macros
- Body tab with weight, resting heart rate, sleep and steps
- An Insights tab where you can ask questions about your own data, like "which lifts have stalled?"
- An optional password, so you can open it from your phone on home Wi-Fi

## Setup

You need Python 3.13.

```bash
git clone https://github.com/foshow23/ProjectJim.git
cd ProjectJim
python3 -m venv .venv
.venv/bin/pip install -r requirements-lock.txt
.venv/bin/streamlit run app.py
```

It opens at http://localhost:8501. (In VS Code, use the "ProjectJim" option under Run and Debug. Running `app.py` as a normal Python file doesn't work with Streamlit.)

To use the AI features or set a password, copy `.env.example` to `.env` and fill in:

- `ANTHROPIC_API_KEY` for the meal estimates and the coach. You get one at [console.anthropic.com](https://console.anthropic.com). It's billed separately from a Claude subscription, but it's cheap: about a tenth of a cent per question on Claude Haiku 5.5.
- `USDA_API_KEY` (optional), free from [api.data.gov/signup](https://api.data.gov/signup). Without it, food search is limited to a few searches an hour.
- `PROJECTJIM_PASSWORD` if you want a sign-in screen.

## Getting your data in

Press the ⟳ button at the top of any tab to open Sources, and drop in:

- Garmin: the full data export (request it from Garmin's website), or single .fit/.tcx/.gpx files
- Hevy: Profile → Settings → Export & Import Data → Export Workouts (.csv)
- Lose It!: the data export from your account settings on loseit.com
- Apple Health: in the Health app, tap your profile picture → Export All Health Data

You can import the same files again later. Nothing gets duplicated, and workouts you deleted in Hevy or Garmin disappear here too.

## Using it on your phone

Add a password to `.env` first (it won't start in this mode without one), then run:

```bash
.venv/bin/streamlit run app.py --server.address 0.0.0.0
```

Open the "Network URL" it prints in Safari on your phone. I'd only do this on home Wi-Fi, since the connection isn't encrypted.

## How the merging works

Each app's copy of a workout is saved as its own record, and records get grouped into workouts ([jim/merger.py](jim/merger.py)). Two records count as the same session if they're from different apps, the activity types are compatible, they overlap by at least half the shorter one, and their distances (if both have one) are within 10%. Two records from the same app are never merged, so back-to-back sessions stay separate.

The merged workout gets its timing, heart rate and calories from the watch, and its name and sets from Hevy. Apple Health only fills in: it adds heart rate and calories to workouts from Hevy or Garmin, but never creates a workout of its own. Its copies have no names or sets, so they'd just show up as "Strength".

## How the coach works

The Insights coach uses Claude, and I wanted it to answer only from my data, not make things up ([jim/coach.py](jim/coach.py)):

- Its only tools are four functions that read the app's database. It has no web access.
- The tools do the maths (totals, averages, changes), so the AI just reads numbers off.
- After it answers, Python checks that every number in the answer actually appears in the data it was given. If one doesn't, it gets one chance to rewrite, and anything still unverified is shown in orange.

Each question also comes with a summary of the last four weeks, so simple questions don't need any lookups. Long date ranges get averaged by week or month before they're sent, which keeps it fast and cheap.

## Privacy

Everything is stored locally in `data/projectjim.db`. That folder and `.env` are gitignored. The only things that leave your computer are coach questions and meal photos (sent to Anthropic) and food searches (sent to USDA and Open Food Facts). Dependencies are pinned to exact versions in `requirements-lock.txt` and were checked with pip-audit.

## Tests

```bash
.venv/bin/python -m pytest
```

There are 107 tests, all on made-up data. The AI tests use a fake Claude, so they don't need a key or internet.

## Code layout

```
app.py              Password check, tab bar, which tab is showing
views/              One file per tab
jim/
  db.py             SQLite tables
  merger.py         Matches records from different apps into one workout
  importers/        Hevy, Garmin, Lose It! and Apple Health
  nutrition.py      Food diary, goals, totals
  foods.py          Units and densities
  food_search.py    USDA and Open Food Facts
  estimator.py      AI meal estimates
  coach.py          The Insights coach and the number check
  bodyweight.py     Body weight for pull-ups, dips and push-ups
  login.py          Password screen
  ios.py            The iPhone-style look
tests/
```

## Credits

- Icons: [Material Symbols](https://fonts.google.com/icons) by Google (Apache 2.0), bundled with Streamlit
- Maps: © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, © [CARTO](https://carto.com/attributions), drawn with [deck.gl](https://deck.gl)
- Food data: [USDA FoodData Central](https://fdc.nal.usda.gov) (public domain) and [Open Food Facts](https://world.openfoodfacts.org) (Open Database License)
- Cup and spoon weights: King Arthur Baking's [ingredient weight chart](https://www.kingarthurbaking.com/learn/ingredient-weight-chart)
- AI: [Claude](https://www.anthropic.com/claude) by Anthropic
- Push-up load (64% of body weight): Ebben, W. P., Wurm, B., VanderZanden, T. L., et al. (2011). Kinetic analysis of several variations of push-ups. *Journal of Strength and Conditioning Research*, 25(10), 2891–2894.
- The design follows Apple's iOS conventions and colors, but uses no Apple artwork or SF Symbols.

ProjectJim isn't affiliated with Garmin, Hevy, Lose It! or Apple.

## License

[MIT](LICENSE)

"""The AI coach on the Insights tab: answers questions about your training, food and body.

It is kept to ProjectJim's data in three ways:
1. The only tools it has are the four get_... functions below, which read this app's database.
   It has no web search, so it has nothing else to look at.
2. The system prompt tells it to answer only from that data and to say so when data is missing.
3. After it answers, unverified_numbers() checks that every number in the answer appears in the
   data it was given. If one doesn't, it's asked to rewrite once; anything still unverified is
   shown to you as a warning.

Uses the official Anthropic SDK. The key comes from ANTHROPIC_API_KEY (in .env).
"""

import json
import os
from datetime import date, datetime, time, timedelta

import anthropic

from . import bodyweight, db, formatting, nutrition

# Claude Haiku 5.5: the cheapest current model. Set PROJECTJIM_MODEL in .env to use another.
DEFAULT_MODEL = "claude-haiku-5-5"

# Most questions need one or two lookups. This stops it looping forever.
MAX_LOOKUP_ROUNDS = 6

# Lists longer than this are shortened before they're sent (averaged by week or month, or only the
# most recent kept), so the AI reads less: quicker, cheaper, and the number check stays strict.
MAX_ITEMS = 60

# The instructions sent with every question.
SYSTEM_PROMPT = """\
You are the coach inside ProjectJim, a personal fitness app. You answer questions about the \
user's own training, food and body data.

Rules:
- Use only the data overview in the question and the results of your tools. Never use outside \
facts, typical averages or general fitness knowledge as if it were their data.
- Every number you write must appear in that data. You may round it, but don't calculate new \
numbers yourself: the tools already give totals, averages and changes.
- If the data needed isn't there, say "I don't have that in ProjectJim" and name what would add \
it (a newer Garmin, Hevy, Lose It! or Apple Health export).
- The overview's last_4_weeks often answers the question already: then answer straight away.
- Otherwise look things up with the tools. When you need several lookups, ask for all of them at
  once in the same turn, not one after another. Dates are YYYY-MM-DD.
- Keep answers short: two to five sentences, or a few bullet points. Talk to the user as "you".
- Formatting: only **bold** and "- " bullet points. No tables, headings or numbered lists."""

# The tools the AI may call. Each one describes a get_... function below, so the AI knows what
# it does and what to pass in. "strict" makes sure it always passes exactly these inputs.
TOOLS = [
    {
        "name": "get_workouts",
        "description": "Workouts between two dates, newest first, with totals. kind is run, ride, walk, "
                       "hike, swim, strength, other, or all.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "First day, YYYY-MM-DD"},
                "end": {"type": "string", "description": "Last day, YYYY-MM-DD"},
                "kind": {"type": "string", "enum": ["all", "run", "ride", "walk", "hike", "swim", "strength", "other"]},
            },
            "required": ["start", "end", "kind"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "get_exercise_history",
        "description": "Every session of one lifting exercise between two dates: its sets, most reps and "
                       "estimated one-rep max, plus the change from the first session to the last. Bodyweight "
                       "exercises (pull-ups, dips, push-ups) include the user's body weight that day. If the "
                       "name doesn't match, returns the exercise names that exist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "exercise": {"type": "string", "description": "Part of the exercise name, e.g. bench press"},
                "start": {"type": "string", "description": "First day, YYYY-MM-DD"},
                "end": {"type": "string", "description": "Last day, YYYY-MM-DD"},
            },
            "required": ["exercise", "start", "end"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "get_body_metric",
        "description": "One body measurement between two dates, with first, last, change, average, lowest "
                       "and highest. sleep_hours is per night, steps and active_energy are per day.",
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": ["weight", "body_fat", "resting_hr", "hrv", "vo2max",
                                                      "sleep_hours", "steps", "active_energy"]},
                "start": {"type": "string", "description": "First day, YYYY-MM-DD"},
                "end": {"type": "string", "description": "Last day, YYYY-MM-DD"},
            },
            "required": ["metric", "start", "end"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "get_food_days",
        "description": "Food logged per day between two dates (calories, protein, carbs, fat) and the "
                       "workouts done that day, plus averages and the user's daily goals.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "First day, YYYY-MM-DD"},
                "end": {"type": "string", "description": "Last day, YYYY-MM-DD"},
            },
            "required": ["start", "end"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]

# How each tool is named in the "Looked up" line under an answer.
LOOKUP_NAMES = {
    "get_workouts": "Workouts",
    "get_exercise_history": "Exercise history",
    "get_body_metric": "Body",
    "get_food_days": "Food log",
}

BODY_METRICS = ["weight", "body_fat", "resting_hr", "hrv", "vo2max", "sleep_hours", "steps", "active_energy"]


class CoachError(Exception):
    """Something went wrong, with a message that's safe to show to the user."""


def model():
    """Which Claude model to use."""
    chosen = os.environ.get("PROJECTJIM_MODEL", "").strip()
    if chosen:
        return chosen
    return DEFAULT_MODEL


def is_configured():
    """True when there's an API key in .env."""
    return os.environ.get("ANTHROPIC_API_KEY", "").strip() != ""


# --- Reading the database: the only data the AI can see -----------------------------------------

def start_of_day(day):
    """ "2026-10-07" -> the timestamp at 00:00 that day."""
    return datetime.combine(date.fromisoformat(day), time.min).timestamp()


def end_of_day(day):
    """ "2026-10-07" -> the timestamp at 23:59:59 that day."""
    return datetime.combine(date.fromisoformat(day), time.max).timestamp()


def day_of(timestamp):
    """A timestamp -> "2026-10-07"."""
    return datetime.fromtimestamp(timestamp).date().isoformat()


def summarize(values, digits):
    """First, last, change, average, lowest and highest of a list of numbers, so the AI never has
    to do the maths itself. `digits` is how many decimal places to round to."""
    if len(values) == 0:
        return {"count": 0}
    first = values[0]
    last = values[-1]
    result = {
        "count": len(values),
        "first": round(first, digits),
        "last": round(last, digits),
        "change": round(last - first, digits),
        "average": round(sum(values) / len(values), digits),
        "lowest": round(min(values), digits),
        "highest": round(max(values), digits),
    }
    if first != 0:
        result["change_percent"] = round((last - first) / first * 100, 1)
    return result


def period_of(day, size):
    """Which week or month a "YYYY-MM-DD" day falls in: "2026-W41" or "2026-10"."""
    if size == "week":
        year, week, weekday = date.fromisoformat(day).isocalendar()
        return f"{year}-W{week:02d}"
    return day[:7]


def average_by_period(readings, digits):
    """Daily readings, shortened when there are more than MAX_ITEMS: averaged by week, or by
    month if there are still too many weeks. Returns (the readings to send, "day"/"week"/"month")."""
    if len(readings) <= MAX_ITEMS:
        return readings, "day"
    for size in ["week", "month"]:
        # Put each reading's value in its week's (or month's) list.
        groups = {}
        for reading in readings:
            key = period_of(reading["date"], size)
            if key not in groups:
                groups[key] = []
            groups[key].append(reading["value"])
        if len(groups) <= MAX_ITEMS or size == "month":
            averaged = []
            for key in groups:
                values = groups[key]
                averaged.append({size: key, "average": round(sum(values) / len(values), digits),
                                 "readings": len(values)})
            return averaged, size


def get_workouts(conn, start, end, kind):
    """Workouts between two days (newest first) and their totals."""
    if kind == "all":
        rows = conn.execute("SELECT * FROM workouts WHERE start_ts BETWEEN ? AND ? ORDER BY start_ts DESC",
                            (start_of_day(start), end_of_day(end))).fetchall()
    else:
        rows = conn.execute("SELECT * FROM workouts WHERE start_ts BETWEEN ? AND ? AND kind = ? ORDER BY start_ts DESC",
                            (start_of_day(start), end_of_day(end), kind)).fetchall()

    workouts = []
    total_meters = 0
    total_seconds = 0
    run_meters = 0
    run_seconds = 0
    for row in rows:
        workout = {
            "date": day_of(row["start_ts"]),
            "kind": row["kind"],
            "title": row["title"],
            "minutes": round(row["duration"] / 60),
        }
        if row["distance"]:
            workout["km"] = round(row["distance"] / 1000, 2)
            total_meters += row["distance"]
            if row["kind"] == "run":
                workout["pace"] = formatting.pace(row["distance"], row["duration"])
                run_meters += row["distance"]
                run_seconds += row["duration"]
        if row["avg_hr"]:
            workout["avg_heart_rate"] = round(row["avg_hr"])
        if row["calories"]:
            workout["calories"] = round(row["calories"])
        total_seconds += row["duration"]
        workouts.append(workout)

    totals = {
        "count": len(rows),
        "total_km": round(total_meters / 1000, 1),
        "total_minutes": round(total_seconds / 60),
    }
    if run_meters > 0:
        totals["average_run_pace"] = formatting.pace(run_meters, run_seconds)
    if len(workouts) <= MAX_ITEMS:
        return {"workouts": workouts, "totals": totals}

    # Too many to send one by one: the 30 most recent in full, and totals for every month.
    months = {}
    for row in rows:
        month = day_of(row["start_ts"])[:7]
        if month not in months:
            months[month] = {"count": 0, "meters": 0, "seconds": 0, "run_meters": 0, "run_seconds": 0}
        totals_for_month = months[month]
        totals_for_month["count"] += 1
        totals_for_month["meters"] += row["distance"] or 0
        totals_for_month["seconds"] += row["duration"]
        if row["kind"] == "run" and row["distance"]:
            totals_for_month["run_meters"] += row["distance"]
            totals_for_month["run_seconds"] += row["duration"]
    by_month = []
    for month in months:
        totals_for_month = months[month]
        summary = {"month": month, "count": totals_for_month["count"],
                   "km": round(totals_for_month["meters"] / 1000, 1),
                   "minutes": round(totals_for_month["seconds"] / 60)}
        if totals_for_month["run_meters"] > 0:
            summary["run_pace"] = formatting.pace(totals_for_month["run_meters"], totals_for_month["run_seconds"])
        by_month.append(summary)
    return {"totals": totals, "by_month": by_month, "most_recent_30": workouts[:30]}


def get_exercise_history(conn, exercise, start, end):
    """Every session of one exercise between two days, and how it changed from first to last."""
    unit = db.get_setting(conn, "weight_unit", "kg")
    weights = bodyweight.weigh_ins(conn)
    rows = conn.execute(
        "SELECT w.start_ts, e.exercise_title, e.set_type, e.weight_kg, e.reps"
        " FROM exercise_sets e"
        " JOIN workout_sources s ON s.id = e.source_id"
        " JOIN workouts w ON w.id = s.workout_id"
        " WHERE e.exercise_title LIKE ? AND w.start_ts BETWEEN ? AND ?"
        " ORDER BY w.start_ts, e.set_index",
        ("%" + exercise + "%", start_of_day(start), end_of_day(end)),
    ).fetchall()

    # No match: send back the real exercise names so the AI can try again with one of them.
    if len(rows) == 0:
        names = []
        for row in conn.execute("SELECT exercise_title FROM exercise_sets GROUP BY exercise_title"
                                " ORDER BY COUNT(*) DESC LIMIT 60"):
            names.append(row["exercise_title"])
        return {"found": False, "exercise_names": names}

    # Group the sets into sessions: one per day and exercise name (the search can match more than one
    # exercise, e.g. "bench press" matches barbell and dumbbell).
    sessions = {}
    for row in rows:
        key = day_of(row["start_ts"]) + " " + row["exercise_title"]
        if key not in sessions:
            sessions[key] = []
        sessions[key].append(row)

    results = []
    one_rep_maxes = {}     # exercise name -> the best estimated one-rep max of each session
    most_reps = {}         # exercise name -> the most reps in one set, each session
    for sets in sessions.values():
        name = sets[0]["exercise_title"]
        set_texts = []
        best_one_rep_max = 0
        best_reps = 0
        for one_set in sets:
            if one_set["reps"] is None:
                continue
            # Pull-ups, dips and push-ups count your body weight that day (see jim/bodyweight.py).
            body = bodyweight.body_weight_on(weights, one_set["start_ts"])
            weight = bodyweight.set_load(name, one_set["weight_kg"], body)
            if weight is None:
                weight = 0
            if unit == "lb":
                weight = weight / formatting.KILOGRAMS_PER_POUND
            weight = round(weight, 1)

            # How the set is written: "80 x 6", or "8 reps" when there's no weight.
            if weight > 0:
                text = f"{weight:g} x {one_set['reps']}"
            else:
                text = f"{one_set['reps']} reps"
            if one_set["set_type"] != "normal":
                text += f" ({one_set['set_type']})"
            set_texts.append(text)

            # Warm-ups don't count towards the best set.
            if one_set["set_type"] == "warmup":
                continue
            # Estimated one-rep max (the Epley formula): weight x (1 + reps / 30).
            one_rep_max = weight * (1 + one_set["reps"] / 30)
            if one_rep_max > best_one_rep_max:
                best_one_rep_max = one_rep_max
            if one_set["reps"] > best_reps:
                best_reps = one_set["reps"]

        session = {"date": day_of(sets[0]["start_ts"]), "exercise": name, "sets": set_texts, "most_reps": best_reps}
        if name not in most_reps:
            most_reps[name] = []
        most_reps[name].append(best_reps)
        if best_one_rep_max > 0:
            session["estimated_one_rep_max"] = round(best_one_rep_max, 1)
            if name not in one_rep_maxes:
                one_rep_maxes[name] = []
            one_rep_maxes[name].append(best_one_rep_max)
        results.append(session)

    # Lots of sessions: keep the sets only for the 30 most recent (older ones keep their best numbers).
    if len(results) > MAX_ITEMS:
        for session in results[:-30]:
            del session["sets"]

    # How each exercise changed from its first session to its last.
    progress = {}
    for name in most_reps:
        progress[name] = {"most_reps": summarize(most_reps[name], 0)}
        if name in one_rep_maxes:
            progress[name]["estimated_one_rep_max"] = summarize(one_rep_maxes[name], 1)
    return {"found": True, "unit": unit, "sessions": results, "progress_first_to_last_session": progress}


def get_body_metric(conn, metric, start, end):
    """Every reading of one body measurement between two days, and a summary."""
    weight_unit = db.get_setting(conn, "weight_unit", "kg")
    units = {"weight": weight_unit, "body_fat": "%", "resting_hr": "bpm", "hrv": "ms", "vo2max": "ml/kg/min",
             "sleep_hours": "hours", "steps": "steps", "active_energy": "kcal"}
    # Whole numbers for counts, one decimal place for the rest.
    if metric in ["steps", "active_energy", "resting_hr", "hrv"]:
        digits = 0
    else:
        digits = 1

    rows = conn.execute("SELECT ts, value FROM body_metrics WHERE metric = ? AND ts BETWEEN ? AND ? ORDER BY ts",
                        (metric, start_of_day(start), end_of_day(end))).fetchall()
    readings = []
    values = []
    for row in rows:
        value = row["value"]
        if metric == "weight" and weight_unit == "lb":
            value = value / formatting.KILOGRAMS_PER_POUND
        if metric == "body_fat":
            value = value * 100     # stored as 0 to 1, shown as a percentage
        readings.append({"date": day_of(row["ts"]), "value": round(value, digits)})
        values.append(value)
    readings, grouped_by = average_by_period(readings, digits)
    return {"metric": metric, "unit": units[metric], "summary": summarize(values, digits),
            "readings_per": grouped_by, "readings": readings}


def get_food_days(conn, start, end):
    """What was eaten each logged day between two days, which workouts were done that day, and the
    averages and goals."""
    first_day = date.fromisoformat(start)
    last_day = date.fromisoformat(end)
    number_of_days = (last_day - first_day).days + 1

    # Which workouts happened each day, e.g. {"2026-10-06": ["strength"]}.
    workouts_by_day = {}
    for row in conn.execute("SELECT start_ts, kind FROM workouts WHERE start_ts BETWEEN ? AND ?",
                            (start_of_day(start), end_of_day(end))):
        day = day_of(row["start_ts"])
        if day not in workouts_by_day:
            workouts_by_day[day] = []
        workouts_by_day[day].append(row["kind"])

    days = []
    for day in nutrition.days(conn, last_day, number_of_days):
        if not day.is_logged:
            continue
        days.append({
            "date": day.day,
            "calories": round(day.calories),
            "protein": round(day.protein),
            "carbs": round(day.carbs),
            "fat": round(day.fat),
            "workouts": workouts_by_day.get(day.day, []),
        })

    # Averages for every logged day, for strength-training days, and for the other days.
    strength_days = []
    other_days = []
    for day in days:
        if "strength" in day["workouts"]:
            strength_days.append(day)
        else:
            other_days.append(day)

    goals = {}
    for name, value in nutrition.goals(conn).items():
        goals[name] = round(value)
    result = {
        "days_logged": len(days),
        "averages": average_food(days),
        "averages_on_strength_days": average_food(strength_days),
        "averages_on_other_days": average_food(other_days),
        "daily_goals": goals,
    }
    # Lots of days: only the most recent ones are listed (the averages above still cover them all).
    if len(days) > MAX_ITEMS:
        result["most_recent_days"] = days[-MAX_ITEMS:]
    else:
        result["days"] = days
    return result


def average_food(days):
    """Average calories, protein, carbs and fat over a list of days."""
    if len(days) == 0:
        return {}
    averages = {"days": len(days)}
    for name in ["calories", "protein", "carbs", "fat"]:
        total = 0
        for day in days:
            total += day[name]
        averages[name] = round(total / len(days))
    return averages


def date_range(conn, query, parameters=()):
    """Runs a query that returns (first date, last date) and says whether there's any data."""
    row = conn.execute(query, parameters).fetchone()
    if row[0] is None:
        return {"has_data": False}
    return {"has_data": True, "from": row[0], "to": row[1]}


def overview(conn):
    """Sent with every question: today's date and how far each kind of data goes, so the AI knows
    what exists before it looks anything up."""
    available = {}
    available["workouts"] = date_range(
        conn, "SELECT date(MIN(start_ts), 'unixepoch', 'localtime'), date(MAX(start_ts), 'unixepoch', 'localtime')"
              " FROM workouts")
    available["lifting_sets"] = date_range(
        conn, "SELECT date(MIN(w.start_ts), 'unixepoch', 'localtime'), date(MAX(w.start_ts), 'unixepoch', 'localtime')"
              " FROM exercise_sets e JOIN workout_sources s ON s.id = e.source_id JOIN workouts w ON w.id = s.workout_id")
    available["food_log"] = date_range(conn, "SELECT MIN(day), MAX(day) FROM food_entries")
    for metric in BODY_METRICS:
        available[metric] = date_range(
            conn, "SELECT date(MIN(ts), 'unixepoch', 'localtime'), date(MAX(ts), 'unixepoch', 'localtime')"
                  " FROM body_metrics WHERE metric = ?", (metric,))
    return {"today": date.today().isoformat(), "weight_unit": db.get_setting(conn, "weight_unit", "kg"),
            "data_available": available, "last_4_weeks": last_four_weeks(conn)}


def last_four_weeks(conn):
    """A summary of the last 28 days, so simple questions ("how was my month?") need no lookups."""
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=27)).isoformat()
    summary = {"from": start, "to": end}

    summary["all_workouts"] = get_workouts(conn, start, end, "all")["totals"]
    for kind in ["run", "ride", "strength"]:
        totals = get_workouts(conn, start, end, kind)["totals"]
        if totals["count"] > 0:
            summary[kind] = totals

    for metric in ["weight", "sleep_hours", "resting_hr", "steps"]:
        result = get_body_metric(conn, metric, start, end)["summary"]
        if result["count"] > 0:
            summary[metric] = {"latest": result["last"], "average": result["average"], "change": result["change"]}

    food = get_food_days(conn, start, end)
    if food["days_logged"] > 0:
        summary["food"] = {"days_logged": food["days_logged"], "averages": food["averages"],
                           "daily_goals": food["daily_goals"]}
    return summary


def run_tool(conn, name, tool_input):
    """Runs the get_... function the AI asked for and returns its result as JSON text."""
    if name == "get_workouts":
        result = get_workouts(conn, tool_input["start"], tool_input["end"], tool_input["kind"])
    elif name == "get_exercise_history":
        result = get_exercise_history(conn, tool_input["exercise"], tool_input["start"], tool_input["end"])
    elif name == "get_body_metric":
        result = get_body_metric(conn, tool_input["metric"], tool_input["start"], tool_input["end"])
    elif name == "get_food_days":
        result = get_food_days(conn, tool_input["start"], tool_input["end"])
    else:
        raise ValueError("Unknown tool " + name)
    return json.dumps(result)


def describe_lookup(name, tool_input):
    """ "Workouts (run): 2026-09-01 to 2026-10-14", for the line under an answer."""
    text = LOOKUP_NAMES[name]
    if "exercise" in tool_input:
        text += " (" + tool_input["exercise"] + ")"
    elif "metric" in tool_input:
        text += " (" + tool_input["metric"] + ")"
    elif tool_input.get("kind", "all") != "all":
        text += " (" + tool_input["kind"] + ")"
    return text + ": " + tool_input["start"] + " to " + tool_input["end"]


# --- Checking the answer's numbers ------------------------------------------------------------

MONTHS = ["jan", "january", "feb", "february", "mar", "march", "apr", "april", "may", "jun", "june",
          "jul", "july", "aug", "august", "sep", "sept", "september", "oct", "october", "nov", "november",
          "dec", "december"]


def is_date(word):
    """True for a date written like 2026-10-07."""
    return len(word) == 10 and word[4] == "-" and word[7] == "-"


def numbers_in(text):
    """Every number written in the text, as strings, leaving out dates.
    "You ran 10.5 km on Oct 7 at 6:47 /km" -> ["10.5", "6", "47"]"""
    numbers = []
    words = text.replace(",", "").split()     # "1,650" -> "1650", then split on spaces
    skip_next_word = False
    for word in words:
        word = word.strip('".:;!?()[]{}*')     # drop punctuation around the word
        if skip_next_word:      # the day after a month name, e.g. the 7 in "Oct 7"
            skip_next_word = False
            continue
        if word.lower() in MONTHS:
            skip_next_word = True
            continue
        if is_date(word):
            continue

        # Collect the digits in the word: "80x6" -> "80", "6" and "7.5km" -> "7.5".
        current = ""
        for character in word:
            if character.isdigit() or (character == "." and current != ""):
                current += character
            else:
                if current != "":
                    numbers.append(current.strip("."))
                current = ""
        if current != "":
            numbers.append(current.strip("."))
    return numbers


def unverified_numbers(answer, data, question=""):
    """The numbers in the answer that can't be found in the data (or in the question).

    Rounding is allowed: "82" matches 81.65 in the data, and "2.3" matches 2.35. Minus signs are
    ignored, so "down 2.3" matches a change of -2.35.
    """
    data_numbers = []
    for text in numbers_in(data):
        data_numbers.append(float(text))
    question_numbers = numbers_in(question)

    missing = []
    for text in numbers_in(answer):
        if text in question_numbers:
            continue
        value = float(text)
        # How far off rounding can make a number: 0.5 for "82", 0.05 for "2.3". Plus a tiny bit,
        # because computers store decimals like 2.35 very slightly off.
        decimals = 0
        if "." in text:
            decimals = len(text.split(".")[1])
        allowed = 0.5 / (10 ** decimals) + 0.000001

        found = False
        for number in data_numbers:
            if abs(number - value) <= allowed:
                found = True
                break
        if not found:
            missing.append(text)
    return missing


# --- Asking Claude ----------------------------------------------------------------------------

def call_claude(client, messages):
    """Sends the conversation so far to Claude and returns its reply. Turns errors into messages
    that are safe to show."""
    settings = {
        "model": model(),
        "max_tokens": 16000,
        "system": SYSTEM_PROMPT,
        "tools": TOOLS,
        "messages": messages,
    }
    # Low effort: the model thinks less before answering, which is quicker and cheaper. Reading
    # numbers from the tools doesn't need deep reasoning. (Haiku 4.5 doesn't have this setting.)
    if not model().startswith("claude-haiku-4"):
        settings["output_config"] = {"effort": "low"}
    try:
        # ** passes each key of the dict as a named argument: model=..., max_tokens=..., and so on.
        return client.messages.create(**settings)
    except anthropic.AuthenticationError:
        raise CoachError("The Anthropic API key in .env wasn't accepted.")
    except anthropic.RateLimitError:
        raise CoachError("The AI is busy right now. Try again in a moment.")
    except anthropic.APIStatusError as error:
        raise CoachError(f"The AI isn't available right now ({error.status_code}).")
    except anthropic.APIConnectionError:
        raise CoachError("Couldn't reach the AI. Check your internet connection.")


def answer_with_lookups(client, conn, messages, data, lookups):
    """Lets Claude call tools until it writes an answer, then returns the answer's text.

    Every tool result is added to `data` (for checking numbers later) and a description of
    every call to `lookups` (for showing under the answer).
    """
    for round_number in range(MAX_LOOKUP_ROUNDS):
        reply = call_claude(client, messages)
        # Add Claude's reply to the conversation exactly as it came back (the API needs it unchanged).
        messages.append({"role": "assistant", "content": reply.content})

        if reply.stop_reason == "refusal":
            raise CoachError("The AI couldn't help with that one. Try asking differently.")
        if reply.stop_reason == "max_tokens":
            raise CoachError("The answer got too long. Try a narrower question.")

        # Not asking for a tool means it's done: return the text it wrote.
        if reply.stop_reason != "tool_use":
            text = ""
            for block in reply.content:
                if block.type == "text":
                    text += block.text
            return text.strip()

        # It asked for one or more tools: run each one, then send all the results back together.
        results = []
        for block in reply.content:
            if block.type != "tool_use":
                continue
            try:
                output = run_tool(conn, block.name, block.input)
                data.append(output)
                lookups.append(describe_lookup(block.name, block.input))
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
            except (ValueError, KeyError) as error:
                # e.g. a badly written date: tell the AI so it can try again
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": "Error: " + str(error), "is_error": True})
        messages.append({"role": "user", "content": results})

    raise CoachError("That needed too many lookups. Try a narrower question.")


# How many earlier questions and answers are sent along, so follow-ups ("what about last month?")
# make sense. Older ones are left out to keep each question quick and cheap.
MAX_EARLIER_TURNS = 5


def ask(conn, question, earlier=None, client=None):
    """Asks the coach a question. `earlier` is the chat so far, as a list of
    {"question": ..., "answer": ...} dicts (oldest first). Returns a dict with the answer's
    "text", what it "looked_up", and any "unverified" numbers."""
    if client is None:
        if not is_configured():
            raise CoachError("Add your Anthropic API key as ANTHROPIC_API_KEY in .env to use Insights "
                             "(console.anthropic.com → API Keys).")
        client = anthropic.Anthropic()

    start = json.dumps(overview(conn))
    data = [start]          # everything the AI has been shown, for checking its numbers
    lookups = []

    # The earlier questions and answers first, then the new question with today's overview.
    messages = []
    if earlier is None:
        earlier = []
    for turn in earlier[-MAX_EARLIER_TURNS:]:
        messages.append({"role": "user", "content": turn["question"]})
        messages.append({"role": "assistant", "content": turn["answer"]})
        data.append(turn["answer"])     # its numbers were already checked, so they may be reused
    messages.append({"role": "user", "content": "Data overview: " + start + "\n\nQuestion: " + question})

    text = answer_with_lookups(client, conn, messages, data, lookups)
    unverified = unverified_numbers(text, "\n".join(data), question)

    # One chance to fix numbers that aren't in the data, before the answer is shown with a warning.
    if len(unverified) > 0:
        messages.append({"role": "user", "content":
                         "These numbers in your answer aren't in the data: " + ", ".join(unverified) + ". "
                         "Rewrite the answer using only numbers from the data overview and tool results, "
                         "or leave them out."})
        text = answer_with_lookups(client, conn, messages, data, lookups)
        unverified = unverified_numbers(text, "\n".join(data), question)

    return {"text": text, "looked_up": lookups, "unverified": unverified}

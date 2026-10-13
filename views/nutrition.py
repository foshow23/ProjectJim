"""Nutrition tab: a day's food by meal, totals against your goals, trends and top foods."""

from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from jim import food_sheets, ios, nutrition, sources_sheet, ui

conn = ui.connection()
today = date.today()
selected: date = min(st.session_state.setdefault("nutrition_day", today), today)
goals = nutrition.goals(conn)
sections = nutrition.sections(conn)
day = nutrition.days(conn, selected, 1)[0]

action = ios.large_title("Nutrition", [("add", "add", "Add Food"), ("goals", "track_changes", "Edit Goals"),
                                       ("sources", "sync", "Sources")])
if action == "add":
    food_sheets.add_food(selected, None)
elif action == "goals":
    food_sheets.edit_goals()
elif action == "sources":
    sources_sheet.show()


# --- The day: picker, totals, meals -------------------------------------------------------------

def day_picker() -> str:
    """ "‹ Today ›": steps through days, never past today. Tapping the date opens a calendar."""
    if selected == today:
        title = "Today"
    elif selected == today - timedelta(days=1):
        title = "Yesterday"
    else:
        title = f"{selected:%A, %b} {selected.day}, {selected.year}"
    back_to_today = "" if selected == today else '<button class="ios-day-today" data-id="today">Back to Today</button>'
    return (f'<div class="ios-daypicker"><button data-id="previous" aria-label="Previous Day">{ios.icon("chevron_left", size=28)}'
            f'</button><div class="ios-day"><button class="ios-day-title" data-id="calendar">{ios.icon("calendar_month")}'
            f'{title}</button>{back_to_today}</div><button data-id="next" aria-label="Next Day" '
            f'{"disabled" if selected == today else ""}>{ios.icon("chevron_right", size=28)}</button></div>')


def macro(name: str, value: float, goal: float, unit: str, tint: str, digits: int = 0) -> str:
    return ios.row(name, detail=f"{value:.{digits}f} / {goal:.{digits}f} {unit}", kind="macro",
                   below=ios.progress(value / goal, tint))


def summary() -> list[str]:
    """Calories against the goal, then each macro with its own bar."""
    goal = goals["calories"]
    remaining = goal - day.calories
    over = remaining < 0
    top = (f'<div class="ios-big"><span class="ios-big-number">{day.calories:.0f}</span>'
           f'<span class="ios-secondary">/ {goal:.0f} kcal</span><span class="ios-spacer"></span>'
           f'<span class="ios-small" style="color:var(--ios-{"red" if over else "secondary"})">'
           f'{abs(remaining):.0f} {"over" if over else "left"}</span></div>'
           + ios.progress(day.calories / goal, "red" if over else "green"))
    items = [ios.block(top),
             macro("Protein", day.protein, goals["protein"], "g", "blue"),
             macro("Carbs", day.carbs, goals["carbs"], "g", "orange"),
             macro("Fat", day.fat, goals["fat"], "g", "purple")]
    if day.water > 0:
        items.append(macro("Water", day.water, goals["water"], "L", "cyan", digits=1))
    for name, value, unit in [("Fiber", day.fiber, "g"), ("Sugar", day.sugars, "g"),
                              ("Saturated fat", day.saturated_fat, "g"), ("Sodium", day.sodium, "mg")]:
        if value is not None:
            items.append(ios.row(name, value=f"{value:.0f} {unit}"))
    return items


def food_row(entry) -> str:
    detail = f"{round(entry['quantity'], 2):g} {entry['units'].lower()}"
    if entry["protein"]:
        detail += f" · {round(entry['protein'], 1):g} g protein"
    return ios.row(entry["name"], detail, value=f"{entry['calories']:.0f}", id=f"entry:{entry['id']}",
                   kind="small", two_lines=True, chevron=False)


html = ios.section([day_picker()])
previous = None
if day.is_logged or day.water > 0:
    html += ios.section(summary(), footnote=f"From {day.source}" if day.source else "")
elif previous := nutrition.last_logged_day(conn, selected):
    html += ios.section([ios.row(f"Nothing logged · Go to {previous:%b} {previous.day}, Last Logged Day",
                                 id="last", kind="action")])
for name, entries in nutrition.meals(day, sections):
    calories = sum(entry["calories"] for entry in entries)
    html += ios.section(
        [food_row(entry) for entry in entries] + [ios.row("Add Food", id=f"add:{name}", icon_name="add", kind="action")],
        title=name, trailing=f"{calories:.0f} kcal" if calories else "")
html += ios.section([ios.row("Edit Meals", id="meals", icon_name="format_list_bulleted", kind="action")])

tapped = ios.tap(html, key="nutrition_day_view") or ""
if tapped in ("previous", "next", "today", "last"):
    st.session_state["nutrition_day"] = {"previous": selected - timedelta(days=1), "next": selected + timedelta(days=1),
                                         "today": today, "last": previous}[tapped]
    st.rerun()
elif tapped == "calendar":
    food_sheets.choose_day(selected)
elif tapped == "meals":
    food_sheets.edit_meals()
elif tapped.startswith("add:"):
    food_sheets.add_food(selected, tapped.removeprefix("add:"))
elif tapped.startswith("entry:"):
    food_sheets.edit_entry(int(tapped.removeprefix("entry:")))


# --- Trends -------------------------------------------------------------------------------------

def calorie_chart(days: list[nutrition.Day]) -> alt.LayerChart:
    """A bar per day, green within the goal and red over it, with the goal as a dashed line."""
    goal = goals["calories"]
    frame = pd.DataFrame({"day": [d.day for d in days], "calories": [d.calories for d in days]})
    bars = alt.Chart(frame).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
        x=alt.X("day:O", axis=ios.day_axis([d.day for d in days])),
        y=alt.Y("calories:Q", title=None),
        color=alt.condition(alt.datum.calories > goal, alt.value(ios.color("red")), alt.value(ios.color("green"))),
        tooltip=[alt.Tooltip("day:O", title="Day"), alt.Tooltip("calories:Q", title="Calories", format=".0f")],
    )
    line = alt.Chart(pd.DataFrame({"goal": [goal]})).mark_rule(strokeDash=[4, 4], color=ios.color("secondary")).encode(y="goal:Q")
    return bars + line


if any(d.is_logged for d in nutrition.days(conn, selected, 90)):
    with ios.card("trends", title="Trends", footnote="Averages count only days with food logged."):
        ranges = {"Week": 7, "Month": 30, "3 Months": 90}
        length = ranges[st.segmented_control("Range", list(ranges), default="Week", key="trend_range",
                                             label_visibility="collapsed", width="stretch") or "Week"]
        days = nutrition.days(conn, selected, length)
        logged = [d for d in days if d.is_logged]
        ios.chart(calorie_chart(days), height=200)
        if logged:
            ios.show(ios.rows([
                ios.row("Average calories", value=f"{sum(d.calories for d in logged) / len(logged):.0f} kcal"),
                ios.row("Average protein", value=f"{sum(d.protein for d in logged) / len(logged):.0f} g"),
                ios.row("Days logged", value=f"{len(logged)} of {len(days)}"),
                ios.row("Days within goal", value=str(sum(d.calories <= goals["calories"] for d in logged))),
            ]))
        else:
            ios.show(ios.rows([ios.row("Nothing logged in this range.", kind="muted")]))

top = nutrition.top_foods(conn, selected, 30)
if top:
    ios.show(ios.section(
        [ios.row(food["name"], f"{food['times']}× · {food['protein']:.0f} g protein", value=f"{food['calories']:.0f} kcal",
                 kind="small", two_lines=True) for food in top],
        title="Top Foods · Last 30 Days"))

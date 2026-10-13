"""Insights tab: where an AI agent will summarize progress and nutrition and answer questions.

For now it's the shell: this week's numbers (what the agent will read) and a question box that
isn't switched on yet.
"""

from datetime import date, datetime, time, timedelta

import streamlit as st

from jim import formatting, ios, nutrition, sources_sheet, ui

conn = ui.connection()
if ios.large_title("Insights", [("sources", "sync", "Sources")]) == "sources":
    sources_sheet.show()

week_start = datetime.combine(date.today() - timedelta(days=6), time.min).timestamp()
workouts = conn.execute("SELECT kind, distance FROM workouts WHERE start_ts >= ?", (week_start,)).fetchall()
cardio_distance = sum(w["distance"] or 0 for w in workouts if w["kind"] != "strength")
logged = [day for day in nutrition.days(conn, date.today(), 7) if day.is_logged]
calories = f"{sum(day.calories for day in logged) / len(logged):.0f} kcal" if logged else "No data"

summary = ios.section([
    ios.row("Workouts", value=str(len(workouts))),
    ios.row("Cardio distance", value=formatting.distance(cardio_distance) if cardio_distance else "0 km"),
    ios.row("Strength sessions", value=str(sum(w["kind"] == "strength" for w in workouts))),
    ios.row("Average calories eaten", value=calories),
], title="Last 7 Days", footnote="This is the data your insights will be based on.")

coming = ios.section([ios.block(
    f'<div class="ios-unavailable" style="padding:20px 8px">{ios.icon("auto_awesome")}<h3>Coming Soon</h3>'
    "<p>An AI coach will summarize your training and nutrition here and spot trends across them.</p></div>")],
    title="Insights")

suggestions = ["How has my running pace changed this month?", "Am I eating enough protein on lifting days?",
               "Which lifts have stalled?"]
questions = ios.section([ios.row(text, icon_name="chat_bubble", tint="secondary", kind="muted plain") for text in suggestions],
                        title="Ask a Question")
ios.show(summary + coming + questions)

with st.container(horizontal=True, vertical_alignment="center"):
    st.text_input("Question", placeholder="Ask about your progress…", disabled=True, label_visibility="collapsed")
    st.button("Ask", icon=":material/arrow_upward:", disabled=True)

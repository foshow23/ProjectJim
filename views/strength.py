"""Strength tab: Hevy's sets merged with the watch's heart rate and calories."""

import streamlit as st

from jim import db, ios, sources_sheet, ui, workout_views

conn = ui.connection()
if ios.pushed("strength"):
    workout_views.show_detail(conn, "strength", back="Strength")
    st.stop()

if ios.large_title("Strength", [("sources", "sync", "Sources")]) == "sources":
    sources_sheet.show()

workouts = conn.execute("SELECT * FROM workouts WHERE kind = 'strength' ORDER BY start_ts DESC").fetchall()
if not workouts:
    ios.show(ios.unavailable(
        "fitness_center", "No Strength Workouts",
        "Export your workouts from Hevy (Profile → Settings → Export & Import Data → Export Workouts), then "
        "import the file here."))
    if st.button("Import Hevy Export", type="primary", width="stretch"):
        sources_sheet.show()
    st.stop()

workout_views.show_list("strength", workouts, workout_views.strength_stats(conn),
                        db.get_setting(conn, "weight_unit", "kg"))

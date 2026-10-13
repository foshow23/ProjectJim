"""Cardio tab: runs, rides, walks, hikes and swims from every source, one row per real workout."""

import streamlit as st

from jim import ios, sources_sheet, ui, workout_views
from jim.models import KIND_PLURALS

conn = ui.connection()
if ios.pushed("cardio"):
    workout_views.show_detail(conn, "cardio", back="Cardio")
    st.stop()

if ios.large_title("Cardio", [("sources", "sync", "Sources")]) == "sources":
    sources_sheet.show()

workouts = conn.execute("SELECT * FROM workouts WHERE kind != 'strength' ORDER BY start_ts DESC").fetchall()
if not workouts:
    ios.show(ios.unavailable(
        "directions_run", "No Cardio Yet",
        "Import your Garmin data export, or single activity files, to bring in your runs, rides, walks and "
        "hikes with their GPS routes."))
    if st.button("Import Garmin Files", type="primary", width="stretch"):
        sources_sheet.show()
    st.stop()

# Only offer filters for kinds that actually have workouts.
present = [kind for kind in ["run", "ride", "walk", "hike", "swim", "other"] if any(w["kind"] == kind for w in workouts)]
if len(present) > 1:
    chosen = st.segmented_control("Show", ["All"] + [KIND_PLURALS[kind] for kind in present], default="All",
                                  key="cardio_kind", persist_state="page", label_visibility="collapsed",
                                  width="stretch")
    if chosen and chosen != "All":
        workouts = [w for w in workouts if KIND_PLURALS[w["kind"]] == chosen]

workout_views.show_list("cardio", workouts, {}, "kg")

"""The Sources sheet: import files, see what each source has brought in, and credits."""

from __future__ import annotations

from html import escape

import streamlit as st

from . import db, ios, ui
from .importers import garmin_export
from .models import GARMIN, HEVY


def link_row(title: str, url: str, icon_name: str) -> str:
    """A row that opens a website in a new tab."""
    return (f'<a class="ios-row action has-icon" href="{escape(url, quote=True)}" target="_blank" rel="noopener" '
            f'style="text-decoration:none">{ios.icon(icon_name)}<div class="ios-body"><div class="ios-title-line">'
            f'<span class="ios-title">{escape(title)}</span></div></div></a>')


@st.dialog("Sources", on_dismiss="rerun")
def show() -> None:
    conn = ui.connection()

    def count(query: str, *parameters) -> int:
        return conn.execute(query, parameters).fetchone()[0]

    with ios.card("import", title="Import Files",
                  footnote="Hevy workout CSV · Garmin data export (.zip) or single activities (.gpx, .tcx, .fit) · "
                           "Lose It! export (.zip) · Apple Health export (.zip). Importing again is safe: what's "
                           "already here is updated, never duplicated."):
        uploads = st.file_uploader("Drop any export here. ProjectJim works out what each file is.",
                                   type=["csv", "zip", "gpx", "tcx", "fit"], accept_multiple_files=True)
        if uploads and st.button("Import", type="primary", width="stretch"):
            for upload in uploads:
                bar = st.progress(0.0, text=f"Importing {upload.name}…")
                try:
                    app, message = ui.import_upload(conn, upload.name, upload.getvalue(),
                                                    progress=lambda fraction, text: bar.progress(fraction, text=text))
                    st.success(f"**{app}** · {upload.name}: {message}")
                except Exception as error:  # show the reason for this file and carry on with the rest
                    st.warning(f"Couldn't import {upload.name}: {error}")
                finally:
                    bar.empty()

    imported = db.get_setting(conn, "apple_health_import") or {}
    health = ios.section([
        ios.row("Apps seen", value=", ".join(imported.get("sources", [])) or "None yet"),
        ios.row("Body measurements", value=f"{count('SELECT COUNT(*) FROM body_metrics'):,}"),
    ], title="Apple Health", footnote=(
        "Only body measurements come from Apple Health: weight, body fat, sleep, resting heart rate, HRV, "
        "VO2 max, steps and active calories. Workouts come from Garmin and Hevy, and food from Lose It!, "
        "directly. On your iPhone open Health, tap your profile picture, then Export "
        "All Health Data. Send the export.zip to this computer and import it above. Each import replaces the "
        "last, so re-export whenever you want fresh data."))

    garmin = ios.section([
        ios.row("Activities", value=str(count("SELECT COUNT(*) FROM workout_sources WHERE source=?", GARMIN))),
        ios.row("With a recorded track", value=str(count(
            "SELECT COUNT(*) FROM workout_sources WHERE source=? AND route IS NOT NULL", GARMIN))),
        link_row("Request Full Export from Garmin", garmin_export.REQUEST_URL, "mail"),
        link_row("Open Garmin Connect", "https://connect.garmin.com/modern/activities", "language"),
    ], title="Garmin", footnote=(
        "Full history: request your data export from Garmin (it's emailed as a .zip), then import the zip above. "
        "Every activity comes in with GPS routes and heart rate. One activity: on connect.garmin.com open it, "
        "choose ⚙ → Export Original (or Export to TCX), and import the file. Activities deleted or re-timed in "
        "Garmin are updated here, never duplicated."))

    hevy = ios.section([
        ios.row("Workouts imported", value=str(count("SELECT COUNT(*) FROM workout_sources WHERE source=?", HEVY))),
        ios.row("Sets", value=f"{count('SELECT COUNT(*) FROM exercise_sets'):,}"),
    ], title="Hevy", footnote=(
        "In Hevy: Profile → Settings → Export & Import Data → Export Workouts, then import the CSV above. "
        "Workouts you deleted in Hevy are removed here too."))

    lose_it = ios.section([
        ios.row("Foods logged", value=f"{count('SELECT COUNT(*) FROM food_entries WHERE source=?', 'Lose It!'):,}"),
        ios.row("Days", value=str(count("SELECT COUNT(DISTINCT day) FROM food_entries WHERE source=?", "Lose It!"))),
        ios.row("Custom foods", value=str(count("SELECT COUNT(*) FROM saved_foods WHERE source=?", "Lose It!"))),
        link_row("Open Lose It! Website", "https://www.loseit.com", "language"),
    ], title="Lose It!", footnote=(
        "On loseit.com, open your account settings and export your data, then import the zip above. A newer "
        "export replaces the days it covers, so re-importing never duplicates."))

    credits = ios.section([
        ios.row("Icons", "Material Symbols by Google, Apache License 2.0", kind="small"),
        ios.row("Maps", "© OpenStreetMap contributors, © CARTO. Drawn with deck.gl.", kind="small"),
        ios.row("Food data", "USDA FoodData Central (public domain) and Open Food Facts (Open Database License)",
                kind="small"),
        ios.row("Weight of a cup or spoonful", "King Arthur Baking's ingredient weight chart", kind="small"),
        ios.row("AI estimates and the coach", "Claude by Anthropic", kind="small"),
        ios.row("Push-up load", "64% of body weight: Ebben et al. (2011), Journal of Strength and "
                "Conditioning Research", kind="small"),
    ], title="Credits", footnote="The look follows Apple's iOS design; ProjectJim isn't affiliated with any of "
                                 "the apps or services above.")

    ios.show(health + garmin + hevy + lose_it + credits)

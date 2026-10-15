"""Helpers shared by the Streamlit pages."""

from __future__ import annotations

import io
import sqlite3
import zipfile

import streamlit as st
from dotenv import load_dotenv

from . import db
from .importers import apple_health, garmin_export, garmin_files, hevy, loseit

load_dotenv()   # API keys from .env


@st.cache_resource
def connection() -> sqlite3.Connection:
    return db.connect()


def import_upload(conn: sqlite3.Connection, name: str, data: bytes, progress=None) -> tuple[str, str]:
    """Imports one uploaded file, working out what it is. Returns (app name, what happened)."""
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if extension == "csv":
        result = hevy.import_csv(conn, data.decode("utf-8-sig"))
        lines = [f"Read {result.workouts} workouts ({result.sets} sets)."]
        if result.ingest.inserted:
            lines.append(f"New: {result.ingest.inserted}.")
        if result.ingest.merged_into_existing:
            lines.append(f"Matched with a watch recording: {result.ingest.merged_into_existing}.")
        if result.ingest.updated:
            lines.append(f"Already here, refreshed: {result.ingest.updated}.")
        if result.removed:
            lines.append(f"Deleted in Hevy, removed: {result.removed}.")
        return "Hevy", " ".join(lines)

    if extension in ("gpx", "tcx", "fit"):
        return "Garmin", _garmin_file_message(garmin_files.import_file(conn, data, name))

    if extension == "zip":
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as error:
            raise ValueError("That zip file couldn't be opened.") from error
        if loseit.is_loseit_export(archive):
            result = loseit.import_archive(conn, archive)
            lines = [f"Imported {result.entries} foods across {result.days} days ({result.first_day} to {result.last_day})."]
            if result.custom_foods:
                lines.append(f"{result.custom_foods} of your custom foods added to Your Foods.")
            lines.append("Importing a newer export replaces these days, so nothing is duplicated.")
            return "Lose It!", " ".join(lines)
        if garmin_export.is_garmin_export(archive):
            result = garmin_export.import_archive(conn, archive, progress)
            lines = [f"Read {result.activities} Garmin activities, {result.with_routes} with GPS routes."]
            if result.ingest.inserted:
                lines.append(f"New: {result.ingest.inserted}.")
            if result.ingest.merged_into_existing:
                lines.append(f"Matched with workouts from Hevy: {result.ingest.merged_into_existing}.")
            if result.ingest.updated:
                lines.append(f"Already here, refreshed: {result.ingest.updated}.")
            if result.removed:
                lines.append(f"Deleted in Garmin, removed: {result.removed}.")
            return "Garmin", " ".join(lines)
        if apple_health.is_apple_health_export(archive):
            result = apple_health.import_archive(conn, archive)
            lines = [f"Read {result.measurements:,} body measurements."]
            if result.sources:
                lines.append("From: " + ", ".join(result.sources[:8]) + ".")
            return "Apple Health", " ".join(lines)
        # Garmin Connect's "Export Original" is a zip holding the watch's FIT recording.
        fit_name = next((n for n in archive.namelist() if n.lower().endswith(".fit")), None)
        if fit_name:
            return "Garmin", _garmin_file_message(garmin_files.import_file(conn, archive.read(fit_name), name))
        raise ValueError("That zip isn't a Garmin, Lose It! or Apple Health export.")

    raise ValueError("Unsupported file type. Use a Hevy .csv, a Garmin .gpx/.tcx/.fit, or an export .zip.")


def _garmin_file_message(result: garmin_files.FileResult) -> str:
    if result.has_gps:
        detail = "GPS route saved."
    elif result.has_heart_rate:
        detail = "Heart rate saved, no GPS in this file."
    else:
        detail = "No GPS in this file."
    matched = " Matched with the same workout from another source." if result.matched_existing else ""
    return f"{result.title}: {detail}{matched}"

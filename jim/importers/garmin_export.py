"""The archive from Garmin's "Export Your Data" (garmin.com -> Account -> Data Management).

It holds every activity's summary (DI-Connect-Fitness/*_summarizedActivities.json) plus the
watch's original FIT recordings (DI-Connect-Uploaded-Files/*.zip) for GPS routes and heart rate.
"""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from dataclasses import dataclass, field

from .. import merger
from ..models import GARMIN, SourceWorkout, has_gps
from . import garmin_files

REQUEST_URL = "https://www.garmin.com/account/datamanagement/exportdata/"


class NotGarminExport(ValueError):
    def __init__(self):
        super().__init__("That zip isn't a Garmin data export. Request one at garmin.com → Account → "
                         "Data Management → Export Your Data.")


@dataclass
class ExportResult:
    activities: int = 0
    with_routes: int = 0
    ingest: merger.IngestResult = field(default_factory=merger.IngestResult)
    removed: int = 0            # Garmin activities in ProjectJim that are no longer in Garmin


def is_garmin_export(archive: zipfile.ZipFile) -> bool:
    return any(name.endswith("summarizedActivities.json") for name in archive.namelist())


def summaries(data: bytes) -> list[SourceWorkout]:
    """Garmin's summary file uses its own units: milliseconds, centimeters and kilojoules."""
    try:
        activities = json.loads(data)[0]["summarizedActivitiesExport"]
    except (ValueError, LookupError, TypeError) as error:
        raise NotGarminExport() from error

    items = []
    for activity in activities:
        start_ms = activity.get("beginTimestamp") or activity.get("startTimeGmt")
        if start_ms is None:
            continue
        start = round(start_ms / 1000)
        distance = (activity.get("distance") or 0) / 100
        calories = (activity.get("calories") or 0) / 4.184
        activity_id = activity.get("activityId")
        elevation = activity.get("elevationGain")
        items.append(SourceWorkout(
            source=GARMIN,
            external_id=garmin_files.key_for_activity_id(activity_id) if activity_id is not None else str(start),
            app_name=garmin_files.APP_NAME, kind=garmin_files.kind_for(activity.get("activityType")),
            title=activity.get("name"), start_ts=float(start),
            duration=(activity.get("duration") or activity.get("elapsedDuration") or 0) / 1000,
            distance=distance if distance > 0 else None, calories=calories if calories > 0 else None,
            avg_hr=activity.get("avgHr"), elevation_gain=elevation / 100 if elevation is not None else None,
        ))
    return items


def import_archive(conn: sqlite3.Connection, archive: zipfile.ZipFile, progress=None) -> ExportResult:
    """Imports every activity. `progress(fraction, text)` is called while reading watch files."""
    summary_name = next((n for n in archive.namelist() if n.endswith("summarizedActivities.json")), None)
    if summary_name is None:
        raise NotGarminExport()
    items = summaries(archive.read(summary_name))

    # Attach each watch recording to its summary by start time.
    by_start = {int(item.start_ts): item for item in items}
    for uploads_name in archive.namelist():
        if "DI-Connect-Uploaded-Files" not in uploads_name or not uploads_name.endswith(".zip"):
            continue
        inner = zipfile.ZipFile(io.BytesIO(archive.read(uploads_name)))
        fit_names = [n for n in inner.namelist() if n.lower().endswith(".fit")]
        for index, name in enumerate(fit_names):
            if progress and index % 50 == 0:
                progress(index / max(len(fit_names), 1), f"Reading watch files ({index} of {len(fit_names)})")
            data = inner.read(name)
            if not garmin_files.is_fit_activity(data):
                continue
            try:
                recording = garmin_files.parse_fit(data)
            except garmin_files.NotAnActivityFile:
                continue
            start = int(recording.start_ts)
            # Summary and recording usually share the exact second; allow a little slack.
            match = next((by_start[start + offset] for offset in _offsets(60) if start + offset in by_start), None)
            if match is not None:
                match.route = recording.route
                match.avg_hr = match.avg_hr or recording.avg_hr
                if match.elevation_gain is None:
                    match.elevation_gain = recording.elevation_gain
            else:
                items.append(recording)     # recorded but missing from the summary file
                by_start[start] = recording

    if not items:
        raise NotGarminExport()
    garmin_files.reconcile(conn, items)
    result = ExportResult(activities=len(items), with_routes=sum(1 for i in items if has_gps(i.route or [])))
    result.ingest = merger.ingest(conn, items)

    # The export holds every activity on the account, so a Garmin Connect record within its date
    # range that isn't in it was deleted in Garmin. Outside the range (activities newer than the
    # export) and records from other apps are left alone.
    earliest, latest = min(i.start_ts for i in items), max(i.start_ts for i in items)
    keep = {i.external_id for i in items}
    result.removed = merger.remove_sources(
        conn, GARMIN,
        lambda r: r["app_name"] == garmin_files.APP_NAME and earliest <= r["start_ts"] <= latest
        and r["external_id"] not in keep,
    )
    merger.consolidate(conn)
    return result


def _offsets(limit: int):
    """0, 1, -1, 2, -2, ... so the closest start time is found first."""
    yield 0
    for offset in range(1, limit + 1):
        yield offset
        yield -offset

"""Garmin Training Center (TCX) parser."""

from __future__ import annotations

import re
from pathlib import Path

from .models import Lap, Point, Recording
from .xmlutil import (
    child,
    children,
    descendant_text,
    local,
    parse_time,
    read_xml,
    text,
    to_float,
    to_int,
)

# e.g. "2013-02-06_08-28-08_Quick trip to Bainbridge for lunch_Other (1).tcx"
FILENAME_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})"
    r"(?:_(?P<time>\d{2}-\d{2}-\d{2}))?"
    r"(?:_(?P<title>.+?))?"
    r"_(?P<label>[A-Za-z]+)(?: \(\d+\))?$"
)


def parse_filename(stem: str) -> tuple[str | None, str | None]:
    """Return (title, sport_label) encoded in an exported TCX file name."""
    m = FILENAME_RE.match(stem)
    if not m:
        return None, None
    return m.group("title"), m.group("label")


def _clean_name(name: str | None) -> str | None:
    """Drop empty names and bare sport labels such as "Other"."""
    name = (name or "").strip()
    if not name or name.lower() in GENERIC_NAMES:
        return None
    return name


GENERIC_NAMES = {"other", "running", "cycling", "biking", "untitled"}


def _ext_value(el, name: str) -> str | None:
    ext = child(el, "Extensions")
    return descendant_text(ext, name) if ext is not None else None


def _parse_point(tp) -> Point:
    pos = child(tp, "Position")
    speed = _ext_value(tp, "Speed")
    cadence = text(tp, "Cadence") or _ext_value(tp, "RunCadence")
    return Point(
        time=parse_time(text(tp, "Time")),
        lat=to_float(text(pos, "LatitudeDegrees")) if pos is not None else None,
        lon=to_float(text(pos, "LongitudeDegrees")) if pos is not None else None,
        ele=to_float(text(tp, "AltitudeMeters")),
        hr=to_int(text(tp, "HeartRateBpm", "Value")),
        cadence=to_int(cadence),
        distance=to_float(text(tp, "DistanceMeters")),
        speed=to_float(speed),
    )


def _parse_lap(lap_el) -> tuple[Lap, list[Point]]:
    cadence = text(lap_el, "Cadence") or _ext_value(lap_el, "AvgRunCadence")
    lap = Lap(
        start_time=parse_time(lap_el.get("StartTime")),
        timer_s=to_float(text(lap_el, "TotalTimeSeconds")),
        distance_m=to_float(text(lap_el, "DistanceMeters")),
        calories=to_int(text(lap_el, "Calories")),
        avg_hr=to_float(text(lap_el, "AverageHeartRateBpm", "Value")),
        max_hr=to_int(text(lap_el, "MaximumHeartRateBpm", "Value")),
        max_speed=to_float(text(lap_el, "MaximumSpeed")),
        avg_cadence=to_float(cadence),
        intensity=text(lap_el, "Intensity"),
        trigger=text(lap_el, "TriggerMethod"),
    )
    points = []
    for track in children(lap_el, "Track"):
        points.extend(_parse_point(tp) for tp in children(track, "Trackpoint"))
    return lap, points


def parse_tcx(path: Path) -> list[Recording]:
    root, sha = read_xml(path)
    title, label = parse_filename(path.stem)
    recordings = []
    activities = [el for el in root.iter() if local(el.tag) == "Activity"]
    for idx, act in enumerate(activities):
        laps, points = [], []
        for lap_el in children(act, "Lap"):
            lap, pts = _parse_lap(lap_el)
            laps.append(lap)
            points.extend(pts)
        creator = child(act, "Creator")
        # Garmin Connect exports the activity title as the first line of Notes and the
        # user's description as the remaining lines.
        notes = text(act, "Notes") or ""
        note_title, _, description = notes.partition("\n")
        recordings.append(
            Recording(
                source_file=path.name,
                source_format="tcx",
                segment=idx,
                external_id=text(act, "Id"),
                device=text(creator, "Name") if creator is not None else None,
                device_sport=act.get("Sport"),
                file_label=label,
                name=_clean_name(note_title) or _clean_name(title),
                notes=description.strip() or None,
                points=points,
                laps=laps,
                file_sha256=sha,
            )
        )
    return recordings

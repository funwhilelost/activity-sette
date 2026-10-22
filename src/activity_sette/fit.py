"""Garmin FIT (Flexible and Interoperable Data Transfer) parser.

FIT is the native binary format recorded by Garmin devices; TCX/GPX are exports derived
from it. A FIT activity file holds ``record`` messages (the per-second samples), ``lap``
messages, and one ``session`` message per recorded activity. Files with several sessions
are split into one recording per session, partitioned by timestamp.
"""

from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone
from pathlib import Path

from fitparse import FitFile
from fitparse.processors import FitFileDataProcessor

from .models import Lap, Point, Recording

# FIT enum values rendered as strings by fitparse.
GENERIC_SPORTS = {"generic", "unknown"}


class _DataProcessor(FitFileDataProcessor):
    """fitparse's default processor applies profile scale/offset (altitude, speed,
    distance) but leaves positions in semicircles; convert those to degrees ourselves.
    (Its StandardUnitsDataProcessor would also re-scale speed/distance to km/h and km,
    which the shared model does not expect.)"""

    def process_units_semicircles(self, field_data):
        if field_data.value is not None:
            field_data.value *= 180.0 / (2**31)
        field_data.units = "deg"


def _utc(value) -> datetime | None:
    """fitparse returns naive datetimes; they are UTC by definition."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def _device(vals: dict) -> str | None:
    maker = vals.get("manufacturer")
    product = vals.get("product_name") or vals.get("garmin_product") or vals.get("product")
    parts = [str(maker).capitalize() if maker else None, str(product) if product is not None else None]
    return " ".join(p for p in parts if p) or None


def _parse_point(vals: dict) -> Point:
    return Point(
        time=_utc(vals.get("timestamp")),
        lat=vals.get("position_lat"),
        lon=vals.get("position_long"),
        ele=vals.get("enhanced_altitude") or vals.get("altitude"),
        hr=vals.get("heart_rate"),
        cadence=vals.get("cadence"),
        distance=vals.get("distance"),
        speed=vals.get("enhanced_speed") or vals.get("speed"),
    )


def _parse_lap(vals: dict) -> Lap:
    return Lap(
        start_time=_utc(vals.get("start_time")),
        timer_s=vals.get("total_timer_time"),
        distance_m=vals.get("total_distance"),
        calories=vals.get("total_calories"),
        avg_hr=vals.get("avg_heart_rate"),
        max_hr=vals.get("max_heart_rate"),
        max_speed=vals.get("max_speed"),
        avg_cadence=vals.get("avg_cadence") or vals.get("avg_running_cadence"),
        trigger=vals.get("lap_trigger") or vals.get("trigger"),
    )


def parse_fit(path: Path) -> list[Recording]:
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    fit = FitFile(io.BytesIO(data), data_processor=_DataProcessor())

    points: list[Point] = []
    laps: list[Lap] = []
    sessions: list[dict] = []
    device = None
    for msg in fit.get_messages():
        vals = msg.get_values()
        if msg.name == "record":
            points.append(_parse_point(vals))
        elif msg.name == "lap":
            laps.append(_parse_lap(vals))
        elif msg.name == "session":
            sessions.append(vals)
        elif msg.name in ("device_info", "file_id") and device is None:
            device = _device(vals)

    if not sessions and not (points or laps):
        return []

    recordings = []
    # One recording per session; points/laps are assigned to the session whose
    # [start_time, timestamp] window contains them.
    for idx, session in enumerate(sessions or [{}]):
        start, end = _utc(session.get("start_time")), _utc(session.get("timestamp"))
        if len(sessions) > 1 and start is not None:
            end = end or max((p.time for p in points if p.time), default=start)
            sess_points = [p for p in points if p.time is None or start <= p.time <= end]
            sess_laps = [l for l in laps if l.start_time is None or start <= l.start_time <= end]
        else:
            sess_points, sess_laps = points, laps

        sport = session.get("sport")
        # fitparse returns the raw int for enum values its profile doesn't know
        # (e.g. sport ids added by newer devices); those carry no usable label.
        if not isinstance(sport, str):
            sport = None
        elif sport in GENERIC_SPORTS:
            sport = "other"
        # Garmin Connect TCX exports use the activity start time as the TCX Id; using the
        # same value here lets TCX and FIT copies of one activity merge by external_id.
        ext_start = start or (sess_laps[0].start_time if sess_laps else None) or next(
            (p.time for p in sess_points if p.time), None
        )
        recordings.append(
            Recording(
                source_file=path.name,
                source_format="fit",
                segment=idx,
                external_id=ext_start.strftime("%Y-%m-%dT%H:%M:%SZ") if ext_start else None,
                device=device,
                device_sport=sport,
                points=sess_points,
                laps=sess_laps,
                file_sha256=sha,
            )
        )
    return recordings

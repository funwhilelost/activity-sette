"""GPX parser.

Handheld units (e.g. GPSMAP) write an "active log" that can hold many outings
separated by hours or weeks, so tracks are split wherever the gap between
consecutive points exceeds ``split_gap_s``.
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import Point, Recording
from .xmlutil import (
    child,
    children,
    descendant_text,
    parse_time,
    read_xml,
    text,
    to_float,
    to_int,
)

_NOISE_RE = re.compile(r"(?i)track|[\d\W_]+")


def _meaningful(name: str | None) -> bool:
    return bool(name) and len(_NOISE_RE.sub("", name)) >= 2


def _parse_point(pt) -> Point:
    ext = child(pt, "extensions")
    hr = cad = speed = None
    if ext is not None:
        hr = to_int(descendant_text(ext, "hr"))
        cad = to_int(descendant_text(ext, "cad"))
        speed = to_float(descendant_text(ext, "speed"))
    return Point(
        time=parse_time(text(pt, "time")),
        lat=to_float(pt.get("lat")),
        lon=to_float(pt.get("lon")),
        ele=to_float(text(pt, "ele")),
        hr=hr,
        cadence=cad,
        speed=speed,
    )


def split_on_gaps(points: list[Point], split_gap_s: float) -> list[list[Point]]:
    segments: list[list[Point]] = []
    current: list[Point] = []
    last_time = None
    for p in points:
        if (
            current
            and p.time is not None
            and last_time is not None
            and (p.time - last_time).total_seconds() > split_gap_s
        ):
            segments.append(current)
            current = []
        current.append(p)
        if p.time is not None:
            last_time = p.time
    if current:
        segments.append(current)
    return segments


def parse_gpx(path: Path, split_gap_s: float = 6 * 3600) -> list[Recording]:
    root, sha = read_xml(path)
    creator = root.get("creator")
    tracks = children(root, "trk")

    track_name = next((text(t, "name") for t in tracks if text(t, "name")), None)
    gpx_type = next((text(t, "type") for t in tracks if text(t, "type")), None)
    name = track_name if _meaningful(track_name) else (path.stem if _meaningful(path.stem) else None)

    points: list[Point] = []
    for trk in tracks:
        for seg in children(trk, "trkseg"):
            points.extend(_parse_point(pt) for pt in children(seg, "trkpt"))
    # Tracks/segments are occasionally written out of order; sort timed points.
    if all(p.time is not None for p in points):
        points.sort(key=lambda p: p.time)

    segments = split_on_gaps(points, split_gap_s) if points else []
    recordings = []
    for idx, seg_points in enumerate(segments):
        seg_name = name
        if name and len(segments) > 1:
            seg_name = f"{name} (part {idx + 1})"
        recordings.append(
            Recording(
                source_file=path.name,
                source_format="gpx",
                segment=idx,
                device=creator,
                gpx_type=gpx_type,
                name=seg_name,
                points=seg_points,
                file_sha256=sha,
            )
        )
    return recordings

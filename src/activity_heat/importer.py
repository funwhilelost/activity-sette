"""Parse a directory of activity files and (re)build the SQLite database."""

from __future__ import annotations

import logging
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import sqlite_utils

from . import sports, tz
from .gpx import parse_gpx
from .metrics import richness, summarize
from .models import Recording
from .tcx import parse_tcx

log = logging.getLogger(__name__)

PARSERS = {".tcx": "tcx", ".gpx": "gpx"}


@dataclass
class Parsed:
    rec: Recording
    summary: dict
    role: str = "candidate"  # primary | duplicate | fragment | empty
    activity_id: str | None = None
    note: str | None = None


@dataclass
class ImportResult:
    files: list[dict] = field(default_factory=list)
    recordings: list[Parsed] = field(default_factory=list)
    activities: list[dict] = field(default_factory=list)


def iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def discover(paths: list[Path]) -> list[Path]:
    found = []
    for p in paths:
        if p.is_dir():
            found.extend(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in PARSERS)
        elif p.suffix.lower() in PARSERS:
            found.append(p)
    return sorted(found)


def parse_files(files: list[Path], cfg: sports.Config, result: ImportResult) -> None:
    for path in files:
        fmt = PARSERS[path.suffix.lower()]
        entry = {"file": path.name, "path": str(path), "format": fmt, "size_bytes": path.stat().st_size}
        try:
            recs = parse_tcx(path) if fmt == "tcx" else parse_gpx(path, cfg.gpx_split_gap_hours * 3600)
        except Exception as e:  # malformed XML etc. - record and keep going
            log.warning("Failed to parse %s: %s", path.name, e)
            result.files.append({**entry, "status": "error", "error": str(e), "recordings": 0})
            continue
        entry["sha256"] = recs[0].file_sha256 if recs else None
        result.files.append({**entry, "status": "ok" if recs else "empty", "error": None, "recordings": len(recs)})
        for rec in recs:
            parsed = Parsed(rec, summarize(rec))
            s = parsed.summary
            if s["start_utc"] is None:
                parsed.role, parsed.note = "empty", "no timestamps"
            elif (s["elapsed_s"] or 0) < cfg.min_activity_seconds and (s["timer_s"] or 0) < cfg.min_activity_seconds:
                parsed.role, parsed.note = "fragment", "too short"
            elif rec.source_format == "gpx" and s["point_count"] < cfg.min_activity_points:
                parsed.role, parsed.note = "fragment", "too few points"
            result.recordings.append(parsed)


def _overlap_ratio(a: Parsed, b: Parsed) -> float:
    s1, e1 = a.summary["start_utc"], a.summary["end_utc"]
    s2, e2 = b.summary["start_utc"], b.summary["end_utc"]
    shorter = min((e1 - s1).total_seconds(), (e2 - s2).total_seconds())
    inter = (min(e1, e2) - max(s1, s2)).total_seconds()
    if shorter <= 0:
        return 1.0 if s1 == s2 else 0.0
    return max(0.0, inter) / shorter


def cluster(recs: list[Parsed], ratio: float) -> list[list[Parsed]]:
    """Group recordings that describe the same real-world activity."""
    items = sorted(recs, key=lambda p: p.summary["start_utc"])
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_external: dict[str, int] = {}
    for i, a in enumerate(items):
        ext = a.rec.external_id
        if ext:
            if ext in by_external:
                parent[find(i)] = find(by_external[ext])
            else:
                by_external[ext] = i
        for j in range(i + 1, len(items)):
            b = items[j]
            if b.summary["start_utc"] > a.summary["end_utc"]:
                break
            if _overlap_ratio(a, b) >= ratio:
                parent[find(j)] = find(i)
    groups: dict[int, list[Parsed]] = {}
    for i, p in enumerate(items):
        groups.setdefault(find(i), []).append(p)
    return list(groups.values())


def build_activities(result: ImportResult, cfg: sports.Config) -> None:
    candidates = [p for p in result.recordings if p.role == "candidate"]
    used_ids: set[str] = set()
    for group in cluster(candidates, cfg.duplicate_overlap_ratio):
        group.sort(key=lambda p: richness(p.rec, p.summary), reverse=True)
        primary = group[0]
        s = primary.summary
        base_id = s["start_utc"].strftime("%Y%m%dT%H%M%SZ")
        activity_id, n = base_id, 2
        while activity_id in used_ids:
            activity_id, n = f"{base_id}-{n}", n + 1
        used_ids.add(activity_id)

        for i, p in enumerate(group):
            p.activity_id = activity_id
            p.role = "primary" if i == 0 else "duplicate"

        recs = [p.rec for p in group]
        sport = sports.resolve(recs, cfg, activity_id)
        zone, tz_source = tz.timezone_for(s["start_lat"], s["start_lon"], cfg.default_timezone)
        local = s["start_utc"].astimezone(zone)
        name = next((r.name for r in recs if r.name), None)
        notes = next((r.notes for r in recs if r.notes), None)

        row = {
            "id": activity_id,
            "name": name,
            **sport,
            "start_utc": iso(s["start_utc"]),
            "end_utc": iso(s["end_utc"]),
            "start_local": local.strftime("%Y-%m-%dT%H:%M:%S"),
            "timezone": zone.key,
            "timezone_source": tz_source,
            "local_date": local.strftime("%Y-%m-%d"),
            "year": local.year,
            "month": local.strftime("%Y-%m"),
            "iso_week": "%d-W%02d" % local.isocalendar()[:2],
            "weekday": local.strftime("%A"),
            "hour": local.hour,
        }
        summary = {k: v for k, v in s.items() if k not in ("start_utc", "end_utc")}
        row.update(summary)
        row.update(
            {
                "distance_km": round(s["distance_m"] / 1000, 3) if s["distance_m"] else None,
                "duration_s": s["moving_s"] or s["timer_s"] or s["elapsed_s"],
                "description": notes,
                "source_format": primary.rec.source_format,
                "source_file": primary.rec.source_file,
                "device": primary.rec.device,
                "device_sport": primary.rec.device_sport,
                "recording_count": len(group),
            }
        )
        result.activities.append(row)
    result.activities.sort(key=lambda r: r["start_utc"])


def _column_types(rows: list[dict]) -> dict[str, type]:
    """Infer column types from the first non-null value across *all* rows.

    sqlite-utils only samples the first batch, which makes sparse columns (e.g. heart
    rate missing from early activities) end up as TEXT.
    """
    types: dict[str, type] = {}
    for row in rows:
        for k, v in row.items():
            if k not in types and v is not None:
                types[k] = int if isinstance(v, bool) else type(v)
    for row in rows[:1]:
        for k in row:
            types.setdefault(k, str)
    return types


def _write(db: sqlite_utils.Database, result: ImportResult, include_trackpoints: bool) -> None:
    db["source_files"].insert_all(result.files, pk="file", alter=True, columns=_column_types(result.files))
    db["activities"].insert_all(
        result.activities, pk="id", alter=True, columns=_column_types(result.activities)
    )

    rec_rows = []
    for p in result.recordings:
        s, r = p.summary, p.rec
        rec_rows.append(
            {
                "key": r.key,
                "file": r.source_file,
                "segment": r.segment,
                "activity_id": p.activity_id,
                "role": p.role,
                "note": p.note,
                "format": r.source_format,
                "device": r.device,
                "external_id": r.external_id,
                "name": r.name,
                "file_label": r.file_label,
                "device_sport": r.device_sport,
                "gpx_type": r.gpx_type,
                "start_utc": iso(s["start_utc"]),
                "end_utc": iso(s["end_utc"]),
                "elapsed_s": s["elapsed_s"],
                "distance_m": s["distance_m"],
                "point_count": s["point_count"],
                "gps_point_count": s["gps_point_count"],
                "hr_point_count": s["hr_point_count"],
            }
        )
    db["recordings"].insert_all(
        rec_rows,
        pk="key",
        alter=True,
        columns=_column_types(rec_rows),
        foreign_keys=[("activity_id", "activities", "id")],
    )

    primaries = [p for p in result.recordings if p.role == "primary"]
    lap_rows = []
    for p in primaries:
        for i, lap in enumerate(p.rec.laps):
            lap_rows.append(
                {
                    "activity_id": p.activity_id,
                    "lap_index": i,
                    "start_utc": iso(lap.start_time),
                    "timer_s": lap.timer_s,
                    "distance_m": lap.distance_m,
                    "calories": lap.calories,
                    "avg_hr": lap.avg_hr,
                    "max_hr": lap.max_hr,
                    "max_speed_ms": lap.max_speed,
                    "avg_cadence": lap.avg_cadence,
                    "intensity": lap.intensity,
                    "trigger": lap.trigger,
                }
            )
    db["laps"].create(
        {
            "activity_id": str, "lap_index": int, "start_utc": str, "timer_s": float, "distance_m": float,
            "calories": int, "avg_hr": float, "max_hr": int, "max_speed_ms": float, "avg_cadence": float,
            "intensity": str, "trigger": str,
        },
        pk=("activity_id", "lap_index"),
        foreign_keys=[("activity_id", "activities", "id")],
    )
    db["laps"].insert_all(lap_rows)

    db["trackpoints"].create(
        {
            "activity_id": str, "seq": int, "time_utc": str, "elapsed_s": float, "lat": float, "lon": float,
            "elevation_m": float, "heart_rate": int, "cadence": int, "distance_m": float, "speed_ms": float,
        },
        pk=("activity_id", "seq"),
        foreign_keys=[("activity_id", "activities", "id")],
    )
    if include_trackpoints:
        def rows():
            for p in primaries:
                start = p.summary["start_utc"]
                for i, pt in enumerate(p.rec.points):
                    yield {
                        "activity_id": p.activity_id,
                        "seq": i,
                        "time_utc": iso(pt.time),
                        "elapsed_s": (pt.time - start).total_seconds() if pt.time else None,
                        "lat": pt.lat,
                        "lon": pt.lon,
                        "elevation_m": pt.ele,
                        "heart_rate": pt.hr,
                        "cadence": pt.cadence,
                        "distance_m": pt.distance,
                        "speed_ms": pt.speed,
                    }

        db["trackpoints"].insert_all(rows(), batch_size=5000)

    for table, cols in [
        ("activities", ["start_utc"]), ("activities", ["sport"]), ("activities", ["local_date"]),
        ("activities", ["year"]), ("recordings", ["activity_id"]), ("recordings", ["file"]),
    ]:
        db[table].create_index(cols, if_not_exists=True)
    db["activities"].enable_fts(["name", "description"], create_triggers=True)


def run_import(
    paths: list[Path], db_path: Path, cfg: sports.Config, include_trackpoints: bool = True
) -> ImportResult:
    result = ImportResult()
    files = discover(paths)
    log.info("Parsing %d files", len(files))
    parse_files(files, cfg, result)
    build_activities(result, cfg)

    # Build into a scratch DB, then copy over the target with SQLite's backup API so a
    # running Datasette instance sees the complete new data in one step.
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=db_path.parent) as tmp:
        tmp_db = Path(tmp) / "build.db"
        db = sqlite_utils.Database(tmp_db)
        _write(db, result, include_trackpoints)
        db.conn.commit()
        dest = sqlite3.connect(db_path)
        try:
            db.conn.backup(dest)
        finally:
            dest.close()
            db.close()
    return result

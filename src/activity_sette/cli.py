"""Command line interface: ``activity-sette import`` and ``activity-sette inspect``."""

from __future__ import annotations

import argparse
import collections
import logging
import os
import sys
from pathlib import Path

from . import sports, tz
from .importer import parse_rec, run_import
from .metrics import summarize

DEFAULT_CONFIG = Path(os.environ.get("ACTIVITY_HEAT_CONFIG", "config/sports.toml"))
DEFAULT_DB = Path(os.environ.get("ACTIVITY_HEAT_DB", "data/activities.db"))


def _load_config(path: Path | None) -> sports.Config:
    if path and path.exists():
        return sports.Config.load(path)
    logging.warning("Config %s not found; using built-in defaults", path)
    return sports.Config()


def cmd_import(args) -> int:
    cfg = _load_config(args.config)
    if not tz.finder_available():
        logging.warning("timezonefinder not installed; all local times use %s", cfg.default_timezone)
    result = run_import(args.paths, args.db, cfg, include_trackpoints=not args.no_trackpoints)

    roles = collections.Counter(p.role for p in result.recordings)
    statuses = collections.Counter(f["status"] for f in result.files)
    sports_count = collections.Counter(a["sport"] for a in result.activities)
    print(f"Files:       {len(result.files)} ({dict(statuses)})")
    print(f"Recordings:  {len(result.recordings)} ({dict(roles)})")
    print(f"Activities:  {len(result.activities)} -> {args.db}")
    print("By sport:    " + ", ".join(f"{k}={v}" for k, v in sports_count.most_common()))
    conflicts = [a for a in result.activities if a["sport_conflict"]]
    if conflicts:
        print(f"Sport conflicts ({len(conflicts)}), resolve via [overrides] in the config:")
        for a in conflicts:
            print(f"  {a['id']}  {a['sport_candidates']}  ({a['source_file']})")
    for f in result.files:
        if f["status"] == "error":
            print(f"ERROR {f['file']}: {f['error']}")
    return 0


def cmd_inspect(args) -> int:
    cfg = _load_config(args.config)
    for path in args.files:
        recs = parse_rec(path, cfg)
        for rec in recs:
            s = summarize(rec)
            hints = sports.candidates(rec, cfg)
            print(f"== {rec.key}  [{rec.source_format}] device={rec.device!r}")
            print(f"   name={rec.name!r} file_label={rec.file_label!r} device_sport={rec.device_sport!r} gpx_type={rec.gpx_type!r}")
            print(f"   sport hints: {hints or 'none'}")
            for k, v in s.items():
                if k != "route":
                    print(f"   {k:16} {v}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="activity-sette", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help=f"TOML config (default {DEFAULT_CONFIG})")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import", help="Parse files/directories and rebuild the database")
    p.add_argument("paths", nargs="+", type=Path, help="TCX/GPX/FIT files or directories (searched recursively)")
    p.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"SQLite database (default {DEFAULT_DB})")
    p.add_argument("--no-trackpoints", action="store_true", help="Skip the per-point trackpoints table")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("inspect", help="Print what the parser extracts from individual files")
    p.add_argument("files", nargs="+", type=Path)
    p.set_defaults(func=cmd_inspect)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

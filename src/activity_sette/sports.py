"""Configuration loading and sport taxonomy normalization."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .models import Recording

GENERIC = "other"

# Lower number = higher priority.
PRIORITY = {"override": 0, "rule": 1, "file_label": 2, "gpx_type": 3, "weak_rule": 4, "device_sport": 5}


@dataclass
class Config:
    default_timezone: str = "UTC"
    gpx_split_gap_hours: float = 6
    min_activity_seconds: float = 120
    min_activity_points: int = 10
    duplicate_overlap_ratio: float = 0.5
    labels: dict[str, str] = field(default_factory=dict)
    rules: list[tuple[re.Pattern, str, bool]] = field(default_factory=list)
    categories: dict[str, str] = field(default_factory=dict)
    overrides: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None) -> "Config":
        if path is None:
            return cls()
        data = tomllib.loads(Path(path).read_text())
        return cls(
            default_timezone=data.get("default_timezone", "UTC"),
            gpx_split_gap_hours=float(data.get("gpx_split_gap_hours", 6)),
            min_activity_seconds=float(data.get("min_activity_seconds", 120)),
            min_activity_points=int(data.get("min_activity_points", 10)),
            duplicate_overlap_ratio=float(data.get("duplicate_overlap_ratio", 0.5)),
            labels={_norm_key(k): v for k, v in data.get("labels", {}).items()},
            rules=[
                (re.compile(r["pattern"], re.IGNORECASE), r["sport"], bool(r.get("weak", False)))
                for r in data.get("rules", [])
            ],
            categories=dict(data.get("categories", {})),
            overrides=dict(data.get("overrides", {})),
        )

    def normalize_label(self, raw: str | None) -> str | None:
        if not isinstance(raw, str) or not raw.strip():
            return None
        key = _norm_key(raw)
        return self.labels.get(key) or re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_") or None

    def category(self, sport: str) -> str:
        return self.categories.get(sport, GENERIC)


def _norm_key(s: str) -> str:
    return re.sub(r"[\s_\-]+", "", s.strip().lower())


def candidates(rec: Recording, cfg: Config) -> list[tuple[int, str, str]]:
    """All (priority, sport, source) sport hints found in one recording."""
    out: list[tuple[int, str, str]] = []
    if rec.source_file in cfg.overrides:
        out.append((PRIORITY["override"], cfg.overrides[rec.source_file], "override"))
    # Free-text descriptions often mention other sports, so only titles and file names count.
    texts = [t for t in (rec.name, Path(rec.source_file).stem) if t]
    for weak in (False, True):
        for pattern, sport, is_weak in cfg.rules:
            if is_weak == weak and any(pattern.search(t) for t in texts):
                source = "weak_rule" if weak else "rule"
                out.append((PRIORITY[source], sport, source))
                break
    for source, raw in (
        ("file_label", rec.file_label),
        ("gpx_type", rec.gpx_type),
        ("device_sport", rec.device_sport),
    ):
        sport = cfg.normalize_label(raw)
        if sport and sport != GENERIC:
            out.append((PRIORITY[source], sport, source))
    return out


def resolve(recs: list[Recording], cfg: Config, activity_id: str) -> dict:
    """Pick a sport for an activity from all of its recordings (primary first)."""
    hints: list[tuple[int, int, str, str]] = []
    if activity_id in cfg.overrides:
        hints.append((PRIORITY["override"], -1, cfg.overrides[activity_id], "override"))
    for order, rec in enumerate(recs):
        hints.extend((prio, order, sport, source) for prio, sport, source in candidates(rec, cfg))
    if not hints:
        sport, source, conflict = GENERIC, None, False
    else:
        hints.sort()
        best_prio, _, sport, source = hints[0]
        conflict = len({s for p, _, s, _ in hints if p == best_prio}) > 1
    labels = sorted({s for _, _, s, _ in hints})
    return {
        "sport": sport,
        "sport_category": cfg.category(sport),
        "sport_source": source,
        "sport_conflict": conflict,
        "sport_candidates": ", ".join(labels) or None,
    }

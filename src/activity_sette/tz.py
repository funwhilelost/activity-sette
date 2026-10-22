"""Resolve the local timezone of an activity from its position, if possible."""

from __future__ import annotations

from functools import lru_cache
from zoneinfo import ZoneInfo

try:  # optional dependency
    from timezonefinder import TimezoneFinder

    _finder = TimezoneFinder()
except Exception:  # pragma: no cover - depends on environment
    _finder = None


def finder_available() -> bool:
    return _finder is not None


@lru_cache(maxsize=4096)
def _lookup(lat: float, lon: float) -> str | None:
    if _finder is None:
        return None
    return _finder.timezone_at(lat=lat, lng=lon)


def timezone_for(lat: float | None, lon: float | None, default: str) -> tuple[ZoneInfo, str]:
    """Return (zone, source) where source is "gps" or "default"."""
    if lat is not None and lon is not None:
        name = _lookup(round(lat, 3), round(lon, 3))
        if name:
            return ZoneInfo(name), "gps"
    return ZoneInfo(default), "default"

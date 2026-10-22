"""Format-independent data model shared by the TCX, GPX, and FIT parsers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class Point:
    time: datetime | None = None
    lat: float | None = None
    lon: float | None = None
    ele: float | None = None
    hr: int | None = None
    cadence: int | None = None
    # Cumulative distance reported by the device (TCX/FIT); may exist without GPS (foot pod, speed sensor).
    distance: float | None = None
    speed: float | None = None

    @property
    def has_position(self) -> bool:
        return self.lat is not None and self.lon is not None


@dataclass
class Lap:
    start_time: datetime | None = None
    timer_s: float | None = None
    distance_m: float | None = None
    calories: int | None = None
    avg_hr: float | None = None
    max_hr: int | None = None
    max_speed: float | None = None
    avg_cadence: float | None = None
    intensity: str | None = None
    trigger: str | None = None


@dataclass
class Recording:
    """One parsed activity recording.

    A source file yields one or more recordings (GPX files from handheld units are
    split on long time gaps). Several recordings may later be merged into one
    canonical activity during de-duplication.
    """

    source_file: str
    source_format: str  # "tcx" | "gpx" | "fit"
    segment: int = 0
    external_id: str | None = None
    device: str | None = None
    device_sport: str | None = None  # TCX <Activity Sport=...>
    file_label: str | None = None  # sport label embedded in the file name
    gpx_type: str | None = None  # GPX <trk><type>
    name: str | None = None
    notes: str | None = None
    points: list[Point] = field(default_factory=list)
    laps: list[Lap] = field(default_factory=list)
    file_sha256: str | None = None

    @property
    def timed_points(self) -> list[Point]:
        return [p for p in self.points if p.time is not None]

    @property
    def start_time(self) -> datetime | None:
        times = [p.time for p in self.points if p.time is not None]
        times += [lap.start_time for lap in self.laps if lap.start_time is not None]
        return min(times) if times else None

    @property
    def end_time(self) -> datetime | None:
        times = [p.time for p in self.points if p.time is not None]
        if times:
            return max(times)
        start = self.start_time
        if start is None:
            return None
        total = sum(lap.timer_s or 0 for lap in self.laps)
        return start + timedelta(seconds=total)

    @property
    def key(self) -> str:
        return f"{self.source_file}#{self.segment}"

"""Derive summary metrics from a recording, tolerating missing data streams."""

from __future__ import annotations

from .geo import haversine, route_geojson
from .models import Recording

MAX_SAMPLE_GAP_S = 300  # longer gaps are treated as pauses, not as continuous effort
MOVING_SPEED_MS = 0.5
ELEVATION_HYSTERESIS_M = 5.0


def _elevation_change(elevations: list[float]) -> tuple[float, float]:
    """Ascent/descent with hysteresis to suppress GPS/barometric noise."""
    ascent = descent = 0.0
    if not elevations:
        return ascent, descent
    ref = elevations[0]
    for e in elevations[1:]:
        diff = e - ref
        if diff >= ELEVATION_HYSTERESIS_M:
            ascent += diff
            ref = e
        elif diff <= -ELEVATION_HYSTERESIS_M:
            descent -= diff
            ref = e
    return ascent, descent


def summarize(rec: Recording) -> dict:
    pts = sorted(rec.timed_points, key=lambda p: p.time)
    gps = [p for p in rec.points if p.has_position]
    start, end = rec.start_time, rec.end_time

    # Distance along the GPS track
    gps_distance = 0.0
    for a, b in zip(gps, gps[1:]):
        gps_distance += haversine(a.lat, a.lon, b.lat, b.lon)

    lap_distance = sum(l.distance_m for l in rec.laps if l.distance_m is not None)
    device_distances = [p.distance for p in rec.points if p.distance is not None]
    if lap_distance > 0:
        distance, distance_source = lap_distance, "laps"
    elif device_distances and max(device_distances) > 0:
        distance, distance_source = max(device_distances), "device"
    elif gps_distance > 0:
        distance, distance_source = gps_distance, "gps"
    else:
        distance, distance_source = None, None

    # Moving time and time-weighted heart rate from consecutive samples
    moving_s = 0.0
    have_motion_data = False
    hr_weighted = hr_time = 0.0
    for a, b in zip(pts, pts[1:]):
        dt = (b.time - a.time).total_seconds()
        if dt <= 0 or dt > MAX_SAMPLE_GAP_S:
            continue
        d = None
        if a.distance is not None and b.distance is not None:
            d = b.distance - a.distance
        elif a.has_position and b.has_position:
            d = haversine(a.lat, a.lon, b.lat, b.lon)
        if d is not None:
            have_motion_data = True
            if d / dt >= MOVING_SPEED_MS:
                moving_s += dt
        if a.hr is not None and a.hr > 0:
            hr_weighted += a.hr * dt
            hr_time += dt

    hrs = [p.hr for p in rec.points if p.hr]
    if hr_time > 0:
        avg_hr = hr_weighted / hr_time
    elif hrs:
        avg_hr = sum(hrs) / len(hrs)
    else:
        lap_hr = [(l.avg_hr, l.timer_s or 0) for l in rec.laps if l.avg_hr]
        total = sum(w for _, w in lap_hr)
        avg_hr = sum(h * w for h, w in lap_hr) / total if total else None
    lap_max_hr = [l.max_hr for l in rec.laps if l.max_hr]
    max_hr = max(hrs + lap_max_hr) if (hrs or lap_max_hr) else None

    cads = [p.cadence for p in rec.points if p.cadence]
    lap_cads = [l.avg_cadence for l in rec.laps if l.avg_cadence]
    avg_cadence = (sum(cads) / len(cads)) if cads else (sum(lap_cads) / len(lap_cads) if lap_cads else None)

    elevations = [p.ele for p in pts if p.ele is not None]
    ascent, descent = _elevation_change(elevations)

    timer_s = sum(l.timer_s for l in rec.laps if l.timer_s) or None
    elapsed_s = (end - start).total_seconds() if start and end else None
    moving = moving_s if have_motion_data else None
    duration_for_speed = moving or timer_s or elapsed_s
    calories = sum(l.calories for l in rec.laps if l.calories) or None

    max_speeds = [l.max_speed for l in rec.laps if l.max_speed] + [p.speed for p in rec.points if p.speed]

    lats = [p.lat for p in gps]
    lons = [p.lon for p in gps]
    coords = [(p.lon, p.lat) for p in gps]

    return {
        "start_utc": start,
        "end_utc": end,
        "elapsed_s": round(elapsed_s, 1) if elapsed_s is not None else None,
        "timer_s": round(timer_s, 1) if timer_s else None,
        "moving_s": round(moving, 1) if moving is not None else None,
        "distance_m": round(distance, 1) if distance is not None else None,
        "distance_source": distance_source,
        "gps_distance_m": round(gps_distance, 1) if gps else None,
        "avg_speed_ms": round(distance / duration_for_speed, 3) if distance and duration_for_speed else None,
        "max_speed_ms": round(max(max_speeds), 3) if max_speeds else None,
        "ascent_m": round(ascent, 1) if len(elevations) > 1 else None,
        "descent_m": round(descent, 1) if len(elevations) > 1 else None,
        "min_elevation_m": round(min(elevations), 1) if elevations else None,
        "max_elevation_m": round(max(elevations), 1) if elevations else None,
        "avg_hr": round(avg_hr, 1) if avg_hr else None,
        "max_hr": max_hr,
        "avg_cadence": round(avg_cadence, 1) if avg_cadence else None,
        "calories": calories,
        "point_count": len(rec.points),
        "gps_point_count": len(gps),
        "hr_point_count": len(hrs),
        "lap_count": len(rec.laps),
        "has_gps": len(gps) >= 2,
        "has_hr": bool(hrs) or avg_hr is not None,
        "has_cadence": bool(cads or lap_cads),
        "has_elevation": len(elevations) > 1,
        "start_lat": gps[0].lat if gps else None,
        "start_lon": gps[0].lon if gps else None,
        "end_lat": gps[-1].lat if gps else None,
        "end_lon": gps[-1].lon if gps else None,
        "min_lat": min(lats) if lats else None,
        "max_lat": max(lats) if lats else None,
        "min_lon": min(lons) if lons else None,
        "max_lon": max(lons) if lons else None,
        "route": route_geojson(coords),
    }


# Native device data is preferred over exports when duplicate recordings are otherwise equal.
_FORMAT_RANK = {"fit": 2, "tcx": 1}


def richness(rec: Recording, summary: dict) -> tuple:
    """Sort key used to pick the best recording when several describe one activity."""
    return (
        summary["has_hr"],
        _FORMAT_RANK.get(rec.source_format, 0),
        summary["gps_point_count"],
        summary["point_count"],
        rec.source_file,
    )

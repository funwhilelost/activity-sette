"""Geographic helpers: distances and track simplification."""

from __future__ import annotations

import json
import math

EARTH_RADIUS_M = 6_371_008.8


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, a)))


def _perp_dist(p, a, b) -> float:
    # Planar approximation in degrees, scaled by latitude; fine for simplification.
    k = math.cos(math.radians(a[1]))
    px, py = p[0] * k, p[1]
    ax, ay = a[0] * k, a[1]
    bx, by = b[0] * k, b[1]
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def simplify(coords: list[tuple[float, float]], epsilon: float) -> list[tuple[float, float]]:
    """Iterative Ramer-Douglas-Peucker on (lon, lat) tuples."""
    if len(coords) < 3:
        return list(coords)
    keep = [False] * len(coords)
    keep[0] = keep[-1] = True
    stack = [(0, len(coords) - 1)]
    while stack:
        s, e = stack.pop()
        dmax, idx = 0.0, -1
        for i in range(s + 1, e):
            d = _perp_dist(coords[i], coords[s], coords[e])
            if d > dmax:
                dmax, idx = d, i
        if idx != -1 and dmax > epsilon:
            keep[idx] = True
            stack.append((s, idx))
            stack.append((idx, e))
    return [c for c, k in zip(coords, keep) if k]


def route_geojson(coords: list[tuple[float, float]], max_points: int = 1000) -> str | None:
    """Simplified GeoJSON LineString (or Point) for a list of (lon, lat) tuples."""
    if not coords:
        return None
    if len(coords) == 1:
        lon, lat = coords[0]
        return json.dumps({"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]})
    epsilon = 0.00003  # ~3 m
    simplified = simplify(coords, epsilon)
    while len(simplified) > max_points:
        epsilon *= 2
        simplified = simplify(coords, epsilon)
    return json.dumps(
        {
            "type": "LineString",
            "coordinates": [[round(lon, 6), round(lat, 6)] for lon, lat in simplified],
        },
        separators=(",", ":"),
    )

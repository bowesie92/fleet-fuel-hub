"""The detour arithmetic: where would a fuel stop fit into a route most cheaply, and what does it cost.

In business terms: a truck driving stop 3 to stop 4 that pulls into a station on the way pays
d(stop 3, station) + d(station, stop 4) - d(stop 3, stop 4) extra miles and minutes. We try every gap
between consecutive stops and keep the cheapest one (contract section 6, `ref_detour`).
No network and no database in this file, so it is easy to test.
"""

from __future__ import annotations

import math
from typing import Sequence

METERS_PER_MILE = 1609.344
SECONDS_PER_MINUTE = 60.0
EARTH_RADIUS_MI = 3958.8


def haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line miles between two points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(math.sqrt(a))


def miles_from_line(lat: float, lon: float, line_coords: Sequence[Sequence[float]]) -> float:
    """Straight-line miles from a point to the nearest vertex of a GeoJSON line ([lon, lat] pairs)."""
    return min(haversine_mi(lat, lon, c[1], c[0]) for c in line_coords)


def unique_points(stops: Sequence[dict]) -> tuple[list[tuple[float, float]], list[int]]:
    """Distinct stop locations in first-seen order, plus each stop's index into that list.

    A yard that appears at both ends of a route is one point in the matrix, which keeps it small.
    """
    points: list[tuple[float, float]] = []
    seen: dict[tuple[float, float], int] = {}
    order: list[int] = []
    for s in stops:
        key = (round(float(s["lat"]), 6), round(float(s["lon"]), 6))
        if key not in seen:
            seen[key] = len(points)
            points.append(key)
        order.append(seen[key])
    return points, order


def chunk_size(n_points: int, max_cells: int) -> int:
    """How many stations fit in one full square matrix next to `n_points` stop locations."""
    side = math.isqrt(max_cells)
    room = side - n_points
    if room < 1:
        raise ValueError(f"A route with {n_points} distinct stops cannot fit a station into a {max_cells}-cell matrix.")
    return room


def best_insertion(
    order: Sequence[int],
    station_idx: int,
    meters: Sequence[Sequence[float | None]],
    seconds: Sequence[Sequence[float | None]],
) -> tuple[int, float, float] | None:
    """Cheapest gap for one station: (insert_after_stop, extra_miles, extra_minutes), or None if unroutable.

    `order[i]` is the matrix index of stop i. The gap is picked by extra miles; extra minutes are
    those of the same gap. Tiny negative values (road-network rounding) are floored at zero.
    """
    best: tuple[int, float, float] | None = None
    for i in range(len(order) - 1):
        a, b = order[i], order[i + 1]
        legs = (meters[a][station_idx], meters[station_idx][b], meters[a][b])
        times = (seconds[a][station_idx], seconds[station_idx][b], seconds[a][b])
        if any(v is None for v in legs) or any(v is None for v in times):
            continue
        extra_m = max(0.0, legs[0] + legs[1] - legs[2])
        extra_s = max(0.0, times[0] + times[1] - times[2])
        if best is None or extra_m < best[1] * METERS_PER_MILE:
            best = (i, extra_m / METERS_PER_MILE, extra_s / SECONDS_PER_MINUTE)
    return best

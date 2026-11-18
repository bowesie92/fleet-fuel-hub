"""synth/routes.py: build the fixed delivery routes and their planned schedules.

Business terms: Tucson local routes are made by sweeping around the yard by angle so each
route covers one wedge of the city, then ordering each wedge's customers into a sensible
loop. The Phoenix DC to Tucson yard linehaul route is a fixed out-and-back. Stop lists
never change during the period.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .geo import haversine_mi
from .params import Params, Yard


@dataclass(frozen=True)
class Stop:
    """One planned stop: where, how far from the previous stop, and when (minutes after midnight)."""

    seq: int
    stop_type: str  # 'yard' or 'customer'
    location_id: str
    lat: float
    lon: float
    miles_from_prev: float
    arrive_min: float
    depart_min: float


@dataclass(frozen=True)
class Route:
    """A fixed route: its ID, the yard it belongs to, and the ordered stops."""

    route_id: str
    role: str  # 'local' or 'linehaul'
    start_min: int
    speed_mph: float
    stops: tuple[Stop, ...]


def allocate_sizes(n_routes: int, n_points: int, lo: int, hi: int, rng: np.random.Generator) -> np.ndarray:
    """Pick how many customer stops each route gets, between lo and hi, never more than the points available."""
    if n_points < n_routes * lo:
        raise ValueError(
            f"Only {n_points} customer points are available, but {n_routes} routes need at least "
            f"{n_routes * lo} ({lo} each). Fetch more customer points or lower routes.local_route_count."
        )
    sizes = rng.integers(lo, hi + 1, size=n_routes)
    while sizes.sum() > n_points:
        shrinkable = np.flatnonzero(sizes > lo)
        sizes[rng.choice(shrinkable)] -= 1
    return sizes


def sweep_groups(points: pd.DataFrame, yard: Yard, sizes: np.ndarray, rng: np.random.Generator) -> list[pd.DataFrame]:
    """Split customer points into wedges around the yard, one wedge per route (sweep by angle)."""
    chosen = points.iloc[np.sort(rng.choice(len(points), size=int(sizes.sum()), replace=False))]
    k = np.cos(np.radians(yard.lat))
    angle = np.arctan2(chosen["lat"].to_numpy() - yard.lat, (chosen["lon"].to_numpy() - yard.lon) * k)
    order = np.argsort(angle, kind="stable")
    sorted_angle = angle[order]
    gaps = np.diff(np.concatenate([sorted_angle, [sorted_angle[0] + 2 * np.pi]]))
    cut = (int(np.argmax(gaps)) + 1) % len(order)  # start the sweep after the widest empty wedge
    order = np.roll(order, -cut)
    ordered = chosen.iloc[order].reset_index(drop=True)
    groups, start = [], 0
    for size in sizes:
        groups.append(ordered.iloc[start : start + int(size)].reset_index(drop=True))
        start += int(size)
    return groups


def tour_order(yard: Yard, points: pd.DataFrame) -> list[int]:
    """Order a wedge's customers as a loop from the yard: nearest neighbour, then 2-opt clean-up."""
    lat = np.concatenate([[yard.lat], points["lat"].to_numpy()])
    lon = np.concatenate([[yard.lon], points["lon"].to_numpy()])
    dist = haversine_mi(lat[:, None], lon[:, None], lat[None, :], lon[None, :])
    m = len(points)
    tour, left = [0], set(range(1, m + 1))
    while left:
        nxt = min(left, key=lambda j: (dist[tour[-1], j], j))
        tour.append(nxt)
        left.remove(nxt)
    improved = True
    while improved:
        improved = False
        for i in range(1, m):
            for j in range(i + 1, m + 1):
                a, b, c, d = tour[i - 1], tour[i], tour[j], tour[(j + 1) % (m + 1)]
                if dist[a, c] + dist[b, d] < dist[a, b] + dist[c, d] - 1e-9:
                    tour[i : j + 1] = tour[i : j + 1][::-1]
                    improved = True
    return [t - 1 for t in tour[1:]]


def _schedule(stops: list[dict], start_min: int, speed: float, circuity: float, service: list[float]) -> tuple[Stop, ...]:
    """Fill in planned miles and arrival/departure times for an ordered list of stop dictionaries."""
    out, clock = [], float(start_min)
    for i, s in enumerate(stops):
        if i == 0:
            miles, arrive = 0.0, clock
        else:
            prev = stops[i - 1]
            miles = round(float(haversine_mi(prev["lat"], prev["lon"], s["lat"], s["lon"])) * circuity, 1)
            arrive = clock + miles / speed * 60.0
        depart = arrive + service[i]
        clock = depart
        out.append(Stop(i, s["type"], s["id"], round(s["lat"], 5), round(s["lon"], 5), miles, arrive, depart))
    return tuple(out)


def build_local_routes(p: Params, customers: pd.DataFrame, rng: np.random.Generator) -> list[Route]:
    """Build the Tucson local routes TUC-R01.. : yard, 8 to 12 customers in a loop, back to the yard."""
    sizes = allocate_sizes(p.local_route_count, len(customers), p.stops_min, p.stops_max, rng)
    routes = []
    for n, group in enumerate(sweep_groups(customers, p.tuc, sizes, rng), start=1):
        ordered = group.iloc[tour_order(p.tuc, group)]
        yard_stop = {"type": "yard", "id": p.tuc.id, "lat": p.tuc.lat, "lon": p.tuc.lon}
        stops = [yard_stop]
        stops += [
            {"type": "customer", "id": r.customer_id, "lat": float(r.lat), "lon": float(r.lon)}
            for r in ordered.itertuples()
        ]
        stops.append(dict(yard_stop))
        service = [0.0] + [p.minutes_per_stop] * len(ordered) + [0.0]
        routes.append(
            Route(
                route_id=f"TUC-R{n:02d}",
                role="local",
                start_min=p.local_start_min,
                speed_mph=p.speed_local,
                stops=_schedule(stops, p.local_start_min, p.speed_local, p.circuity, service),
            )
        )
    return routes


def build_linehaul_routes(p: Params) -> list[Route]:
    """Build PHX-L01..: each run is Phoenix DC, the Tucson yard (trailer-swap dwell), then back.

    Runs share the same stops; each leaves Phoenix DC one stagger interval after the previous one.
    """
    phx = {"type": "yard", "id": p.phx.id, "lat": p.phx.lat, "lon": p.phx.lon}
    tuc = {"type": "yard", "id": p.tuc.id, "lat": p.tuc.lat, "lon": p.tuc.lon}
    routes = []
    for n in range(p.linehaul_runs):
        start = p.linehaul_start_min + n * p.linehaul_stagger_min
        routes.append(
            Route(
                route_id=f"PHX-L{n + 1:02d}",
                role="linehaul",
                start_min=start,
                speed_mph=p.speed_linehaul,
                stops=_schedule([phx, tuc, dict(phx)], start, p.speed_linehaul, p.circuity, [0.0, p.dwell_min, 0.0]),
            )
        )
    return routes

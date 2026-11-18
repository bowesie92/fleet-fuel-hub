"""synth/ops.py: simulate every truck, every operating day: drive, burn fuel, refuel.

Business terms: each day a truck runs its route. Fuel burns by miles / MPG. Whenever the
tank is under the refuel threshold when the truck leaves a stop, the driver adds a fuel
stop on the next leg. The driver picks the station by one of three behaviors: sensible
(lowest cost after detour, SPEC section 6), convenient (nearest) or odd (random reachable).
The result is a list of true fill events plus a timeline of moves and stops for telematics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from .geo import haversine_mi, segment_distance_mi
from .params import Params
from .prices import PriceBook
from .routes import Route

MINUTES_PER_HOUR = 60.0
SECONDS_PER_MINUTE = 60


@dataclass
class Fill:
    """One true fuel purchase, before any messiness is added."""

    truck_id: str
    route_id: str
    station_idx: int
    ts: datetime  # local Arizona time, whole minutes
    odometer: float
    gallons: float
    net_price: float  # dollars per gallon actually paid (after any partner discount)
    behavior: str


@dataclass
class YardFill:
    """One fill from a yard's own bulk tank (not a card swipe)."""

    truck_id: str
    yard_id: str
    ts: datetime  # local Arizona time, whole minutes
    gallons: float
    price: float  # dollars per gallon: EIA weekly price minus the bulk discount


@dataclass
class TruckDay:
    """One truck's day as an ordered timeline of ('stop', ...) and ('move', ...) items."""

    truck_id: str
    device_id: str
    day: date
    items: list[tuple] = field(default_factory=list)


@dataclass
class _TruckState:
    odo: float
    fuel: float


def _after(t: datetime, minutes: float) -> datetime:
    """Move a clock time forward by minutes, rounded to whole seconds."""
    return t + timedelta(seconds=round(minutes * SECONDS_PER_MINUTE))


def operating_days(p: Params) -> list[date]:
    """The calendar days trucks run: period start to end, on the configured weekdays."""
    span = (p.end - p.start).days + 1
    days = [p.start + timedelta(days=i) for i in range(span)]
    return [d for d in days if d.weekday() in p.operating_weekdays]


class Simulator:
    """Runs the truck-by-truck, day-by-day simulation."""

    def __init__(self, p: Params, fleet: pd.DataFrame, routes: list[Route], stations: pd.DataFrame,
                 book: PriceBook, rng: np.random.Generator) -> None:
        self.p, self.routes, self.book, self.rng = p, routes, book, rng
        self.fleet_df = fleet
        self.fleet = {r.truck_id: r for r in fleet.itertuples()}
        self.st_lat = stations["lat"].to_numpy(dtype=float)
        self.st_lon = stations["lon"].to_numpy(dtype=float)
        confirmed = (stations["truck_access"] == "confirmed").to_numpy()
        self.eligible = np.ones(len(stations), dtype=bool) if p.use_unverified else confirmed
        if not self.eligible.any():
            raise ValueError("No station with confirmed truck access exists, so trucks have nowhere to fuel.")
        self.day_index = {d: i for i, d in enumerate(book.days)}
        self.behaviors = [b for b, _ in p.behavior_mix]
        self.behavior_cdf = np.cumsum([share for _, share in p.behavior_mix])
        self._leg_cache: dict[tuple[str, int], tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}
        self.onsite_yards = {y.id for y in (p.phx, p.tuc) if y.onsite_fuel}
        self.state: dict[str, _TruckState] = {}
        for truck_id, row in self.fleet.items():
            odo = float(rng.integers(p.odo_min, p.odo_max + 1))
            fuel = float(rng.uniform(p.refuel_below, p.fill_to)) * row.tank_gal
            self.state[truck_id] = _TruckState(odo, fuel)

    # ----- geometry and choice -------------------------------------------------

    def _leg_geometry(self, route: Route, leg: int):
        """For one route leg: station distance to the leg, to each end, and road miles added by a stop there."""
        key = (route.route_id, leg)
        if key not in self._leg_cache:
            a, b = route.stops[leg], route.stops[leg + 1]
            seg = segment_distance_mi(self.st_lat, self.st_lon, a.lat, a.lon, b.lat, b.lon)
            d_a = haversine_mi(a.lat, a.lon, self.st_lat, self.st_lon)
            d_b = haversine_mi(b.lat, b.lon, self.st_lat, self.st_lon)
            d_ab = float(haversine_mi(a.lat, a.lon, b.lat, b.lon))
            extra = np.maximum(d_a + d_b - d_ab, 0.0) * self.p.circuity
            self._leg_cache[key] = (seg, d_a, d_b, extra)
        return self._leg_cache[key]

    def _net_cost(self, gallons: float, price: np.ndarray, extra_mi: np.ndarray, mpg: float, speed: float) -> np.ndarray:
        """SPEC section 6 cost of filling at each candidate: fuel, detour fuel, driver time, wear and tear."""
        p = self.p
        extra_min = extra_mi / speed * MINUTES_PER_HOUR
        cost = gallons * price + extra_mi / mpg * price + extra_min / MINUTES_PER_HOUR * p.driver_cost_per_hour
        if p.include_variable_cost:
            cost = cost + extra_mi * p.variable_cost_per_mile
        return cost

    def _choose_station(self, route: Route, leg: int, day_idx: int, gallons: float, mpg: float) -> tuple[int, str]:
        """Pick a fuel station for the next leg and the behavior that picked it."""
        seg, d_a, _d_b, extra = self._leg_geometry(route, leg)
        cand = np.flatnonzero((seg <= self.p.search_radius_mi) & self.eligible)
        if len(cand) == 0:  # nothing eligible within reach of this leg: take the closest eligible station
            cand = np.array([int(np.argmin(np.where(self.eligible, seg, np.inf)))])
        behavior = self.behaviors[min(int(np.searchsorted(self.behavior_cdf, self.rng.random(), side="right")),
                                      len(self.behaviors) - 1)]
        if behavior == "sensible":
            price = self.book.posted[day_idx, cand] - self.book.discount_cpg[cand] / 100.0
            cost = self._net_cost(gallons, price, extra[cand], mpg, route.speed_mph)
            return int(cand[int(np.argmin(cost))]), behavior
        if behavior == "convenient":
            return int(cand[int(np.argmin(d_a[cand]))]), behavior
        return int(self.rng.choice(cand)), behavior

    def _yard_fill(self, route: Route, truck, state: _TruckState, day_idx: int, depart: datetime,
                   yard_fills: list[YardFill]) -> datetime:
        """Fill at the home yard's own tank before departure, unless that departure is skipped.

        Only runs that start at a yard with onsite_fuel fill here. The fill takes fill_minutes
        and ends at the planned departure. Returns the time the truck began sitting at the yard.
        """
        p = self.p
        if route.role != "linehaul" or route.stops[0].location_id not in self.onsite_yards:
            return depart
        if self.rng.random() < p.yard_skip_rate:
            return depart
        target = float(np.clip(self.rng.normal(p.fill_to, p.fill_sd), p.refuel_below, 1.0)) * truck.tank_gal
        gallons = max(target - state.fuel, 0.0)
        began = _after(depart, -p.fill_minutes)
        price = float(self.book.eia[day_idx]) - p.yard_discount_per_gal
        yard_fills.append(YardFill(truck.truck_id, route.stops[0].location_id, began, gallons, price))
        state.fuel += gallons
        return began

    # ----- one truck on one route for one day ----------------------------------

    def _run_route_day(self, route: Route, truck_id: str, day: date, fills: list[Fill],
                       yard_fills: list[YardFill]) -> TruckDay:
        p, rng = self.p, self.rng
        truck = self.fleet[truck_id]
        state = self.state[truck_id]
        day_idx = self.day_index[day]
        mpg = float(np.clip(rng.normal(truck.mpg_assumed, p.mpg_sd), truck.mpg_assumed - p.mpg_cap_sd * p.mpg_sd,
                            truck.mpg_assumed + p.mpg_cap_sd * p.mpg_sd))
        t = datetime.combine(day, time()) + timedelta(minutes=route.start_min)
        first = route.stops[0]
        tl = TruckDay(truck_id, truck.telematics_device_id, day)
        t_fill = self._yard_fill(route, truck, state, day_idx, t, yard_fills)
        tl.items.append(("stop", t_fill, t, first.lat, first.lon, state.odo))
        pos_lat, pos_lon = first.lat, first.lon

        def drive(lat: float, lon: float, miles: float) -> None:
            """Drive from the current position to (lat, lon) over `miles` road miles."""
            nonlocal t, pos_lat, pos_lon
            t1 = _after(t, miles / route.speed_mph * MINUTES_PER_HOUR)
            odo1 = state.odo + miles
            tl.items.append(("move", t, t1, pos_lat, pos_lon, lat, lon, state.odo, odo1, route.speed_mph))
            t, pos_lat, pos_lon = t1, lat, lon
            state.odo = odo1
            state.fuel -= miles / mpg

        for leg in range(len(route.stops) - 1):
            nxt = route.stops[leg + 1]
            factor = 1.0 + float(rng.uniform(-p.miles_noise, p.miles_noise))
            if state.fuel < p.refuel_below * truck.tank_gal:
                target = float(np.clip(rng.normal(p.fill_to, p.fill_sd), p.refuel_below, 1.0)) * truck.tank_gal
                s, behavior = self._choose_station(route, leg, day_idx, target - state.fuel, mpg)
                _seg, d_a, d_b, _extra = self._leg_geometry(route, leg)
                drive(float(self.st_lat[s]), float(self.st_lon[s]), float(d_a[s]) * p.circuity * factor)
                arrive, arrive_odo = t, state.odo
                gallons = target - state.fuel
                t = _after(arrive, p.fill_minutes)
                tl.items.append(("stop", arrive, t, pos_lat, pos_lon, arrive_odo))
                state.fuel += gallons
                swipe = _after(arrive, p.fill_minutes / 2.0).replace(second=0)
                net = float(self.book.posted[day_idx, s] - self.book.discount_cpg[s] / 100.0)
                fills.append(Fill(truck_id, route.route_id, s, swipe, arrive_odo, gallons, net, behavior))
                drive(nxt.lat, nxt.lon, float(d_b[s]) * p.circuity * factor)
            else:
                drive(nxt.lat, nxt.lon, nxt.miles_from_prev * factor)
            service = nxt.depart_min - nxt.arrive_min
            t_dep = _after(t, service)
            tl.items.append(("stop", t, t_dep, nxt.lat, nxt.lon, state.odo))
            t = t_dep
        return tl

    # ----- the whole period ------------------------------------------------------

    def run(self) -> tuple[list[Fill], list[TruckDay], list[YardFill]]:
        """Simulate every operating day; return card fills, truck-day timelines and yard-tank fills."""
        by_id = {r.route_id: r for r in self.routes}
        fills: list[Fill] = []
        days: list[TruckDay] = []
        yard_fills: list[YardFill] = []
        for day, route_id, truck_id in assignments(self.p, self.routes, self.fleet_df):
            days.append(self._run_route_day(by_id[route_id], truck_id, day, fills, yard_fills))
        return fills, days, yard_fills


def assignments(p: Params, routes: list[Route], fleet: pd.DataFrame) -> list[tuple[date, str, str]]:
    """The (date, route_id, truck_id) rows behind the plan: which truck runs which route each day.

    Local routes rotate through the local trucks day by day (so spares take turns and every
    truck builds history); the linehaul runs rotate through the linehaul trucks (one is spare each day when trucks outnumber runs).
    route_plans.csv and the simulation both read this, so they always agree.
    """
    local_ids = list(fleet.loc[fleet["role"] == "local", "truck_id"])
    line_ids = list(fleet.loc[fleet["role"] == "linehaul", "truck_id"])
    local_routes = [r for r in routes if r.role == "local"]
    line_routes = [r for r in routes if r.role == "linehaul"]
    if local_routes and len(local_ids) < len(local_routes):
        raise ValueError(
            f"{len(local_routes)} local routes run each day but only {len(local_ids)} local trucks exist. "
            "Raise fleet.local_trucks or lower routes.local_route_count."
        )
    if line_routes and len(line_ids) < len(line_routes):
        raise ValueError(
            f"{len(line_routes)} linehaul runs go each day but only {len(line_ids)} linehaul trucks exist. "
            "Raise fleet.linehaul_trucks or lower routes.linehaul_runs_per_day."
        )
    out = []
    for di, day in enumerate(operating_days(p)):
        for k, route in enumerate(local_routes):
            out.append((day, route.route_id, local_ids[(k + di) % len(local_ids)]))
        for k, route in enumerate(line_routes):
            out.append((day, route.route_id, line_ids[(k + di) % len(line_ids)]))
    return out

"""Net savings: what a fill really costs once the detour to reach the station is counted.

In business terms: a cheaper pump is only a saving if the extra miles, the extra driver time
and the extra fuel burned getting there do not eat the difference. This module holds the one
cost formula from SPEC section 6, the break-even gap, the "can the truck actually get there"
test, and the loader that gathers every input from the hub. Nothing here reports a raw
price difference.

    cost(s) = G*p_s + (detour_miles/mpg)*p_s + time cost
              + detour_miles*variable_cost_per_mile      (last term only if configured)

The time cost uses effective minutes (rush-hour multiplier, SPEC section 6, "Time costs").
Schedule risk is not a cost: it only ranks Test route stops (`ranking_penalty_usd` below).
Missed savings, the scout, the Test route options and break-even all call the same functions here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import NamedTuple

import duckdb
import pandas as pd

from fleetfuel import config

MINUTES_PER_DAY = 24 * 60  # a calendar fact
SATURDAY = 5  # datetime.weekday() of the first weekend day

@dataclass(frozen=True)
class SavingsParams:
    """Every savings input, read once from config/assumptions.yaml."""

    driver_cost_per_hour: float
    include_variable_cost: bool
    variable_cost_per_mile: float
    min_saving_threshold_usd: float
    reserve_gal: float
    typical_fill_gal: float
    max_route_day_minutes: float
    fill_to_tank_fraction: float
    include_unverified_default: bool
    peak_windows: tuple[tuple[int, int], ...]  # (start, end) in minutes after midnight; start inclusive, end exclusive
    peak_weekdays_only: bool
    travel_time_index: float
    planning_time_index: float
    use_reliability_buffer: bool
    max_detour_minutes: float
    schedule_risk_usd_per_minute: float  # ranking penalty only, never a cost
    minutes_per_stop: float  # unloading time at a customer stop

    @classmethod
    def from_config(cls, cfg: dict) -> "SavingsParams":
        """Read the savings.* and fueling.* keys; a missing key raises KeyError naming it."""
        return cls(
            driver_cost_per_hour=float(config.get(cfg, "savings.driver_cost_per_hour")),
            include_variable_cost=bool(config.get(cfg, "savings.include_variable_cost")),
            variable_cost_per_mile=float(config.get(cfg, "savings.variable_cost_per_mile")),
            min_saving_threshold_usd=float(config.get(cfg, "savings.min_saving_threshold_usd")),
            reserve_gal=float(config.get(cfg, "savings.reserve_gal")),
            typical_fill_gal=float(config.get(cfg, "savings.typical_fill_gal")),
            max_route_day_minutes=float(config.get(cfg, "savings.max_route_day_minutes")),
            fill_to_tank_fraction=float(config.get(cfg, "fueling.fill_to_tank_fraction")),
            include_unverified_default=bool(config.get(cfg, "savings.include_unverified_truck_access_default")),
            peak_windows=tuple(parse_window(w) for w in config.get(cfg, "time_cost.peak_windows")),
            peak_weekdays_only=bool(config.get(cfg, "time_cost.peak_weekdays_only")),
            travel_time_index=float(config.get(cfg, "time_cost.travel_time_index")),
            planning_time_index=float(config.get(cfg, "time_cost.planning_time_index")),
            use_reliability_buffer=bool(config.get(cfg, "time_cost.use_reliability_buffer")),
            max_detour_minutes=float(config.get(cfg, "time_cost.max_detour_minutes")),
            schedule_risk_usd_per_minute=float(config.get(cfg, "time_cost.schedule_risk_usd_per_minute")),
            minutes_per_stop=float(config.get(cfg, "routes.minutes_per_stop")),
        )

    @property
    def peak_multiplier(self) -> float:
        """The detour-minutes multiplier inside a peak window: the Travel Time Index, or the Planning Time Index when the reliability buffer is on."""
        return self.planning_time_index if self.use_reliability_buffer else self.travel_time_index


class PlannedStop(NamedTuple):
    """When a route plans to reach one stop (minutes after midnight, None if the plan lacks it) and whether it is a customer."""

    arrival_minute: int | None
    is_customer: bool


class Detour(NamedTuple):
    """One way to add a station to a route: insert it after stop N, costing extra miles and minutes."""

    insert_after_stop: int
    extra_miles: float
    extra_minutes: float


# ---------------------------------------------------------------- the formulas

def parse_window(text: str) -> tuple[int, int]:
    """'06:00-09:00' as (360, 540) minutes after midnight."""
    start, end = text.split("-")

    def minutes(hhmm: str) -> int:
        hours, mins = hhmm.strip().split(":")
        return int(hours) * 60 + int(mins)

    return minutes(start), minutes(end)


def is_peak_minute(minute: int, params: SavingsParams) -> bool:
    """True when a minute of the day falls inside a rush-hour window. The day is taken to be a weekday."""
    minute %= MINUTES_PER_DAY
    return any(start <= minute < end for start, end in params.peak_windows)


def is_peak(ts: pd.Timestamp, params: SavingsParams) -> bool:
    """True when the local time falls in a peak window (on a weekday, when peak_weekdays_only is on)."""
    if params.peak_weekdays_only and ts.weekday() >= SATURDAY:
        return False
    return is_peak_minute(ts.hour * 60 + ts.minute, params)


def multiplier(ts: pd.Timestamp, params: SavingsParams) -> float:
    """m(t): the peak multiplier at this local time, otherwise 1."""
    return params.peak_multiplier if is_peak(ts, params) else 1.0


def multiplier_at_minute(minute: int, params: SavingsParams) -> float:
    """m(t) for a planned weekday time given as minutes after midnight."""
    return params.peak_multiplier if is_peak_minute(minute, params) else 1.0


def cost_parts(price: float, miles: float, minutes: float, mpg: float, params: SavingsParams, mult: float) -> dict:
    """Every dollar term of one detour (SPEC section 6): fuel burned, wear and driver time. Schedule risk is not a cost.

    minutes_eff = minutes * mult, and driver time is minutes_eff * driver_cost_per_hour / 60. There is no overtime term.
    """
    eff = minutes * mult
    time_usd = eff * params.driver_cost_per_hour / 60.0
    burned = (miles / mpg) * price
    wear = miles * params.variable_cost_per_mile if params.include_variable_cost else 0.0
    return {"minutes_eff": eff, "time_usd": time_usd, "fuel_burn_usd": burned, "wear_usd": wear, "total": burned + wear + time_usd}


def detour_cost(price: float, miles: float, minutes: float, mpg: float, params: SavingsParams, mult: float) -> float:
    """Dollars of the detour itself: fuel burned getting there, driver time (rush-hour adjusted) and optional wear.

    The multiplier is required: a caller that forgets it gets an error, not a silent off-peak price.
    """
    return cost_parts(price, miles, minutes, mpg, params, mult)["total"]


def ranking_penalty_usd(minutes_eff: float, insert_after_stop: int, last_customer_seq: int | None, params: SavingsParams) -> float:
    """Test route ranking only: effective detour minutes times the schedule-risk rate when the stop comes before the last delivery.

    This is never added to a cost, a saving, a break-even or any dollar figure shown.
    """
    before_last = last_customer_seq is not None and insert_after_stop < last_customer_seq
    return minutes_eff * params.schedule_risk_usd_per_minute if before_last else 0.0


def stop_cost(gallons: float, price: float, miles: float, minutes: float, mpg: float, params: SavingsParams, mult: float) -> float:
    """SPEC section 6: cost of fueling `gallons` at a station, detour included."""
    return gallons * price + detour_cost(price, miles, minutes, mpg, params, mult)


def gap_needed_per_gal(price: float, miles: float, minutes: float, mpg: float, gallons: float, params: SavingsParams, mult: float) -> float:
    """SPEC section 6 break-even gap in dollars per gallon: the detour cost (the same `detour_cost` net savings uses) spread over the fill."""
    return detour_cost(price, miles, minutes, mpg, params, mult) / gallons


def fuel_on_board(tank_gal: float, gallons: float, params: SavingsParams) -> float:
    """Estimate of fuel left when the truck pulled in: it fills to a set share of the tank, so on board = that share minus what it took."""
    return params.fill_to_tank_fraction * tank_gal - gallons


def is_feasible(
    tank_gal: float, gallons: float, mpg: float, detour: Detour, route_base_minutes: float, params: SavingsParams, mult: float,
) -> bool:
    """Can the truck reach the alternative with the reserve kept, does the stop fit in the route day, and is the detour short enough?

    The detour limit (time_cost.max_detour_minutes) judges effective minutes. It applies to alternatives, never to the station actually used.
    """
    eff = detour.extra_minutes * mult
    burn_to_reach = detour.extra_miles / mpg
    enough_fuel = fuel_on_board(tank_gal, gallons, params) - burn_to_reach >= params.reserve_gal
    fits_day = route_base_minutes + eff <= params.max_route_day_minutes
    return enough_fuel and fits_day and eff <= params.max_detour_minutes


def best_insertion(
    options: list[Detour], gallons: float, price: float, mpg: float, params: SavingsParams, mult: float
) -> tuple[float, Detour]:
    """Of the places a station can be inserted on a route, the one with the lowest total cost at this price."""
    scored = [(stop_cost(gallons, price, d.extra_miles, d.extra_minutes, mpg, params, mult), d) for d in options]
    return min(scored, key=lambda item: (item[0], item[1].insert_after_stop))


def best_feasible_insertion(
    options: list[Detour], gallons: float, price: float, mpg: float, tank_gal: float, base_minutes: float, params: SavingsParams,
    mult: float,
) -> tuple[float, Detour] | None:
    """The cheapest insertion point the truck can actually use (fuel reserve kept, route day and detour limit respected); None if no point works."""
    usable = [d for d in options if is_feasible(tank_gal, gallons, mpg, d, base_minutes, params, mult)]
    return best_insertion(usable, gallons, price, mpg, params, mult) if usable else None


# ---------------------------------------------------------------- inputs from the hub

@dataclass
class Inputs:
    """Everything the analytics steps read from the hub, as plain dictionaries for quick lookups."""

    fills: list[dict]  # matched fills on a known route day
    stations: dict[str, dict]  # station_id -> row of dim_station
    posted: dict[tuple[str, date], float]  # (station_id, date) -> posted price
    detours: dict[tuple[str, str], list[Detour]]  # (route_id, station_id) -> insertion options
    route_base_minutes: dict[str, float]
    trucks: dict[str, dict]
    period_start: date
    period_end: date
    skipped: dict[str, int] = field(default_factory=dict)  # why some fills are not analysed
    last_customer_seq: dict[str, int | None] = field(default_factory=dict)  # route_id -> seq of the route's last customer stop
    planned_stops: dict[str, dict[int, "PlannedStop"]] = field(default_factory=dict)  # route_id -> seq -> planned time of that stop

    def stop_minute(self, route_id: str, seq: int, params: SavingsParams) -> int:
        """Minute of the weekday (after midnight) at which a fuel stop inserted after stop `seq` happens.

        That is the stop's planned arrival, plus unloading time when it is a customer. A route whose stop has no
        planned arrival is an error that names the route, never a silent guess.
        """
        stop = self.planned_stops.get(route_id, {}).get(seq)
        if stop is None or stop.arrival_minute is None:
            raise ValueError(
                f"Route {route_id} stop {seq} has no planned_arrival in dim_route.stops_json, so the time of a fuel stop after it "
                "cannot be set. Re-run the normalize step so every stop carries its planned arrival."
            )
        unloading = params.minutes_per_stop if stop.is_customer else 0.0
        return int(round(stop.arrival_minute + unloading)) % MINUTES_PER_DAY

    def net_posted(self, station_id: str, day: date) -> float | None:
        """Posted price on a day, net of the station's partner discount; None if there is no price."""
        posted = self.posted.get((station_id, day))
        if posted is None:
            return None
        return posted - self.stations[station_id]["partner_discount_cpg"] / 100.0

    def base_minutes_for(self, route_id: str) -> float:
        """Planned minutes of a route day with no extra stop. A missing value is an error, never a silent 0."""
        value = self.route_base_minutes.get(route_id)
        if value is None:
            raise ValueError(
                f"Route {route_id} has no base_minutes in dim_route, so the route-day time limit "
                "cannot be checked. Fix the route data instead of assuming 0 minutes."
            )
        return value

    def period_days(self) -> int:
        """Length of the period in days, both ends counted."""
        return (self.period_end - self.period_start).days + 1


def last_customer_seq(stops_json: str) -> int | None:
    """Sequence number of the route's last customer stop (stops labelled CUST-..); None when the route has none."""
    seqs = [int(s["seq"]) for s in json.loads(stops_json) if str(s["location_id"]).startswith("CUST-")]
    return max(seqs) if seqs else None


def planned_stops(stops_json: str) -> dict[int, PlannedStop]:
    """Planned arrival of each stop of a route (contract 11.2), as minutes after midnight, keyed by stop sequence."""
    out: dict[int, PlannedStop] = {}
    for stop in json.loads(stops_json):
        text = stop.get("planned_arrival")
        minute = None
        if text:
            hours, mins = str(text).split(":")[:2]
            minute = int(hours) * 60 + int(mins)
        out[int(stop["seq"])] = PlannedStop(minute, str(stop["location_id"]).startswith("CUST-"))
    return out


def load_detours(frame: pd.DataFrame) -> dict[tuple[str, str], list[Detour]]:
    """Group ref_detour rows by route and station."""
    out: dict[tuple[str, str], list[Detour]] = {}
    for row in frame.itertuples(index=False):
        out.setdefault((row.route_id, row.station_id), []).append(
            Detour(int(row.insert_after_stop), float(row.extra_miles), float(row.extra_minutes))
        )
    return out


def load_inputs(con: duckdb.DuckDBPyConnection, cfg: dict) -> Inputs:
    """Read the hub into lookups. Fills without a station or without a route that day are counted, not guessed."""
    stations = {r["station_id"]: r for r in con.execute("SELECT * FROM dim_station").df().to_dict("records")}
    trucks = {r["truck_id"]: r for r in con.execute("SELECT * FROM dim_truck").df().to_dict("records")}
    route_days = con.execute("SELECT route_id, truck_id, date FROM fact_route_day").df()
    route_of = {(r.truck_id, pd.Timestamp(r.date).date()): r.route_id for r in route_days.itertuples(index=False)}
    prices = con.execute("SELECT station_id, date, posted_price_per_gal FROM ref_station_price").df()
    posted = {(r.station_id, pd.Timestamp(r.date).date()): float(r.posted_price_per_gal) for r in prices.itertuples(index=False)}
    routes = con.execute("SELECT route_id, base_minutes, stops_json FROM dim_route").df()
    last_customer = {r.route_id: last_customer_seq(r.stops_json) for r in routes.itertuples(index=False)}
    planned = {r.route_id: planned_stops(r.stops_json) for r in routes.itertuples(index=False)}
    base_minutes = {r.route_id: float(r.base_minutes) for r in routes.itertuples(index=False) if not pd.isna(r.base_minutes)}
    detours = load_detours(con.execute("SELECT route_id, station_id, insert_after_stop, extra_miles, extra_minutes FROM ref_detour").df())

    skipped = {"unmatched_station": 0, "no_route_that_day": 0}
    fills: list[dict] = []
    for r in con.execute("SELECT * FROM fact_fuel_txn ORDER BY ts_local, txn_id").df().itertuples(index=False):
        day = pd.Timestamp(r.ts_local).date()
        if r.station_id is None or pd.isna(r.station_id):
            skipped["unmatched_station"] += 1
            continue
        route_id = route_of.get((r.truck_id, day))
        if route_id is None:
            skipped["no_route_that_day"] += 1
            continue
        truck = trucks[r.truck_id]
        fills.append({
            "txn_id": r.txn_id, "date": day, "truck_id": r.truck_id, "route_id": route_id,
            "station_id": r.station_id, "gallons": float(r.gallons), "price_paid": float(r.price_per_gal),
            "mpg": float(truck["mpg_assumed"]), "tank_gal": float(truck["tank_gal"]), "ts": pd.Timestamp(r.ts_local),
        })
    return Inputs(
        fills=fills, stations=stations, posted=posted, detours=detours, route_base_minutes=base_minutes,
        trucks=trucks, period_start=pd.Timestamp(config.get(cfg, "period.start_date")).date(),
        period_end=pd.Timestamp(config.get(cfg, "period.end_date")).date(), skipped=skipped,
        last_customer_seq=last_customer, planned_stops=planned,
    )

"""Test route and break-even: what adding each station to a route would cost, and how much cheaper it must be.

In business terms: for each Tucson route and each station that has a precomputed detour, we price
a typical fill there (fuel plus the detour) at the weekday time the stop would happen on that
route, compare it with the station the route's trucks use most, and say in one sentence which stop
is best. Separately, each station gets a break-even gap: the smallest price advantage, in cents a
gallon, that pays for a typical detour to it.

Schedule risk is not a cost. It only nudges the ranking (`ranking_penalty_usd`): stops before the
last delivery rank lower. Every dollar figure here excludes it.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from statistics import median
from typing import NamedTuple

from fleetfuel import config
from fleetfuel.analytics.net_savings import (
    Detour, Inputs, SavingsParams, cost_parts, gap_needed_per_gal, is_peak_minute, multiplier_at_minute, ranking_penalty_usd,
    stop_cost,
)

PRICE_BASIS_TEXT = "average posted net price, last {weeks} weeks of the period"
RANKING_NOTE = "Stops before the last delivery are ranked lower to protect the delivery schedule."
INDEX_SOURCE = "Texas A&M 2025 Urban Mobility Report"
MINUTES_PER_HOUR = 60
NOON_HOUR = 12


def price_basis_weeks(cfg: dict) -> int:
    """Weeks of recent prices used for the test route page (config key savings.price_basis_weeks)."""
    return int(config.get(cfg, "savings.price_basis_weeks"))


def price_period(inputs: Inputs, weeks: int) -> tuple[date, date]:
    """First and last day of the price basis window: the last `weeks` weeks of the period."""
    return inputs.period_end - timedelta(days=7 * weeks - 1), inputs.period_end


def basis_prices(inputs: Inputs, weeks: int) -> dict[str, float]:
    """Average posted price net of partner discount over the last `weeks` weeks of the period, per station."""
    first, last = price_period(inputs, weeks)
    sums: dict[str, list[float]] = {}
    for (sid, day), posted in inputs.posted.items():
        if first <= day <= last and sid in inputs.stations:
            sums.setdefault(sid, []).append(posted - inputs.stations[sid]["partner_discount_cpg"] / 100.0)
    return {sid: sum(v) / len(v) for sid, v in sums.items()}


def _trim(value: float, places: int) -> str:
    """Format a number and drop a trailing .0 so '6.0' reads as '6'."""
    text = f"{value:.{places}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def minutes_text(minutes: float) -> str:
    """Minutes in plain English: 'under 1 minute' below one, else a whole number of minutes."""
    if minutes < 1:
        return "under 1 minute"
    whole = round(minutes)
    return f"{whole} minute" if whole == 1 else f"{whole} minutes"


# ---------------------------------------------------------------- plain-English times and dates

def clock_parts(minute: int) -> tuple[str, str]:
    """Minutes after midnight as ('5', 'pm') or ('5:30', 'pm')."""
    hours, mins = divmod(minute, MINUTES_PER_HOUR)
    suffix = "am" if hours < NOON_HOUR else "pm"
    hour12 = hours % NOON_HOUR or NOON_HOUR
    return (f"{hour12}" if mins == 0 else f"{hour12}:{mins:02d}"), suffix


def clock_text(hhmm: str) -> str:
    """'17:00' as '5 pm', '17:30' as '5:30 pm'."""
    hours, minutes = (int(x) for x in hhmm.split(":"))
    number, suffix = clock_parts(hours * MINUTES_PER_HOUR + minutes)
    return f"{number} {suffix}"


def hhmm(minute: int) -> str:
    """Minutes after midnight as 'HH:MM'."""
    hours, mins = divmod(minute, MINUTES_PER_HOUR)
    return f"{hours:02d}:{mins:02d}"


def window_text(start: int, end: int) -> str:
    """A peak window as '6 to 9 am', or '11 am to 2 pm' when it crosses noon."""
    (n1, s1), (n2, s2) = clock_parts(start), clock_parts(end)
    return f"{n1} to {n2} {s2}" if s1 == s2 else f"{n1} {s1} to {n2} {s2}"


def join_words(items: list[str]) -> str:
    """'a', 'a and b', or 'a, b and c'."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def rush_hour_note(params: SavingsParams) -> str:
    """The one-line rush-hour explanation, built from config: windows, days and the index that multiplies detour minutes."""
    days = "weekdays" if params.peak_weekdays_only else "every day"
    windows = join_words([window_text(a, b) for a, b in params.peak_windows])
    label = "Planning Time Index" if params.use_reliability_buffer else "Travel Time Index"
    return (f"Rush hour, {days} {windows}: detour minutes count {params.peak_multiplier:g} times "
            f"(Tucson {label}, {INDEX_SOURCE}).")


def date_range_text(first: date, last: date) -> str:
    """'Oct 4 to Oct 31, 2026', or with both years when the range crosses a year end."""
    def short(d: date) -> str:
        return f"{d:%b} {d.day}"
    if first.year == last.year:
        return f"{short(first)} to {short(last)}, {last.year}"
    return f"{short(first)}, {first.year} to {short(last)}, {last.year}"


def reason_text(option: dict, gallons: float, is_baseline: bool) -> str:
    """One plain-English sentence explaining the best stop. The dollar figure is the saving, never the ranking score."""
    if is_baseline:
        return "No other station beats the one this route's trucks already use once detour costs are counted."
    saving = f"Saves ${option['net_savings_vs_baseline_usd']:.2f} on a {_trim(gallons, 0)}-gallon fill"
    if round(option["extra_miles"], 1) < 0.1 and option["extra_minutes_effective"] < 1:
        return f"{saving} with almost no extra driving."
    return (
        f"{saving} even after the "
        f"{option['extra_miles']:.1f} extra miles and {minutes_text(option['extra_minutes_effective'])} of extra driving."
    )


# ---------------------------------------------------------------- pricing one station on one route

class Priced(NamedTuple):
    """One insertion point priced at the weekday time its stop happens."""

    total: float
    detour: Detour
    minute: int
    mult: float


def price_insertions(inputs: Inputs, params: SavingsParams, route_id: str, options: list[Detour],
                     gallons: float, price: float, mpg: float) -> list[Priced]:
    """Every insertion point of a station on a route, each priced at the planned time of the stop it follows (contract 11.1)."""
    out = []
    for d in options:
        minute = inputs.stop_minute(route_id, d.insert_after_stop, params)
        mult = multiplier_at_minute(minute, params)
        out.append(Priced(stop_cost(gallons, price, d.extra_miles, d.extra_minutes, mpg, params, mult), d, minute, mult))
    return out


def pick_insertion(inputs: Inputs, params: SavingsParams, route_id: str, options: list[Detour],
                   gallons: float, price: float, mpg: float) -> tuple[Priced, bool]:
    """The cheapest insertion point, preferring ones within max_detour_minutes (effective). Returns (priced, over_max).

    over_max is True only when every insertion point is longer than the limit (then the cheapest is shown but flagged).
    """
    priced = price_insertions(inputs, params, route_id, options, gallons, price, mpg)
    within = [p for p in priced if p.detour.extra_minutes * p.mult <= params.max_detour_minutes]
    best = min(within or priced, key=lambda p: (p.total, p.detour.insert_after_stop))
    return best, not within


def build_option(inputs: Inputs, params: SavingsParams, route_id: str, sid: str, station: dict, price: float,
                 options: list[Detour], mpg: float) -> dict:
    """Price a typical fill at a station on a route: one cost, at the planned weekday time of its cheapest allowed stop."""
    gallons = params.typical_fill_gal
    best, over_max = pick_insertion(inputs, params, route_id, options, gallons, price, mpg)
    detour = best.detour
    eff = detour.extra_minutes * best.mult
    last = inputs.last_customer_seq.get(route_id)
    penalty = ranking_penalty_usd(eff, detour.insert_after_stop, last, params)
    return {
        "_total_raw": best.total, "_penalty_raw": penalty,
        "station_id": sid, "truck_access": station["truck_access"], "insert_after_stop": detour.insert_after_stop,
        "stop_time_local": hhmm(best.minute), "is_peak": is_peak_minute(best.minute, params), "multiplier": best.mult,
        "extra_miles": round(detour.extra_miles, 1), "extra_minutes": round(detour.extra_minutes, 1),
        "extra_minutes_effective": round(eff, 1),
        "price_per_gal": round(price, 3), "fuel_cost_usd": round(gallons * price, 2),
        "detour_cost_usd": round(best.total - gallons * price, 2), "total_cost_usd": round(best.total, 2),
        "before_last_delivery": last is not None and detour.insert_after_stop < last,
        "ranking_penalty_usd": round(penalty, 2), "over_max_detour": bool(over_max),
    }


def saving_vs_baseline(baseline: dict, option: dict) -> float:
    """Dollars saved against the baseline stop: the unrounded totals are compared and rounded once, so cents never drift."""
    return round(baseline["_total_raw"] - option["_total_raw"], 2)


def ranking_score(option: dict, baseline: dict) -> float:
    """Ranking only: the saving over the baseline less how much more schedule risk the option carries than the baseline."""
    return (baseline["_total_raw"] - option["_total_raw"]) - (option["_penalty_raw"] - baseline["_penalty_raw"])


def choose_best(options: list[dict], baseline: dict | None, gallons: float, confirmed_only: bool) -> tuple[str | None, str | None]:
    """The allowed option with the best ranking score, or the baseline when nothing scores above zero.

    Only options that save real dollars over the baseline can win: the schedule penalty reorders money-saving
    stops (and can rule one out), but it never makes a dearer stop the recommendation. The reason quotes the
    dollar saving over the baseline, never the score.
    """
    pool = [o for o in options if (o["truck_access"] == "confirmed" or not confirmed_only) and not o["over_max_detour"]]
    if not pool:
        return None, None
    if baseline is None:
        cheapest = min(pool, key=lambda o: (o["total_cost_usd"], o["station_id"]))
        return cheapest["station_id"], "Lowest total cost for a typical fill once the detour is counted."
    saving = [o for o in pool if baseline["_total_raw"] - o["_total_raw"] > 0]
    if not saving:
        return baseline["station_id"], reason_text(baseline, gallons, True)
    top = min(saving, key=lambda o: (-ranking_score(o, baseline), o["station_id"]))
    if ranking_score(top, baseline) <= 0:
        return baseline["station_id"], reason_text(baseline, gallons, True)
    return top["station_id"], reason_text(top, gallons, False)


def route_entry(inputs: Inputs, params: SavingsParams, route_id: str, prices: dict[str, float], overall: Counter,
                mpg: float) -> dict | None:
    """One route's options, baseline and best stops; None when no station has a detour and a price on this route."""
    options = [
        build_option(inputs, params, route_id, sid, inputs.stations[sid], prices[sid], opts, mpg)
        for (rid, sid), opts in sorted(inputs.detours.items())
        if rid == route_id and sid in prices and sid in inputs.stations
    ]
    if not options:
        return None
    by_id = {o["station_id"]: o for o in options}
    used = Counter(f["station_id"] for f in inputs.fills if f["route_id"] == route_id and f["station_id"] in by_id)
    pool = used or Counter({sid: n for sid, n in overall.items() if sid in by_id})
    baseline = by_id[sorted(pool, key=lambda s: (-pool[s], s))[0]] if pool else None
    for o in options:
        o["net_savings_vs_baseline_usd"] = None if baseline is None else saving_vs_baseline(baseline, o)
    gallons = params.typical_fill_gal
    best_id, best_reason = choose_best(options, baseline, gallons, confirmed_only=True)
    best_all_id, best_all_reason = choose_best(options, baseline, gallons, confirmed_only=False)
    for o in options:
        o.pop("_total_raw")  # internal: unrounded values, only needed for the comparisons above
        o.pop("_penalty_raw")
    return {
        "baseline_station_id": None if baseline is None else baseline["station_id"],
        "best_station_id": best_id, "best_reason": best_reason,
        "best_station_id_incl_unverified": best_all_id, "best_reason_incl_unverified": best_all_reason,
        "options": options,
    }


def best_saving(entry: dict) -> float:
    """Dollars the route's best (confirmed) stop saves over its baseline; 0 when the best is the baseline or there is none."""
    best = next((o for o in entry["options"] if o["station_id"] == entry["best_station_id"]), None)
    if best is None or best["station_id"] == entry["baseline_station_id"]:
        return 0.0
    return float(best["net_savings_vs_baseline_usd"] or 0.0)


def default_route_id(routes: dict[str, dict]) -> str | None:
    """The route whose best stop saves the most over its baseline; ties go to the lowest route id."""
    if not routes:
        return None
    return min(routes, key=lambda rid: (-best_saving(routes[rid]), rid))


def compute_detours(inputs: Inputs, params: SavingsParams, cfg: dict, local_route_ids: list[str]) -> dict:
    """The test route page data: for each Tucson route, every station option and the best stop."""
    weeks = price_basis_weeks(cfg)
    prices = basis_prices(inputs, weeks)
    mpg = float(config.get(cfg, "fleet.local_mpg"))
    overall = Counter(f["station_id"] for f in inputs.fills if f["route_id"] in local_route_ids)
    routes: dict[str, dict] = {}
    for route_id in sorted(local_route_ids):
        entry = route_entry(inputs, params, route_id, prices, overall, mpg)
        if entry is not None:
            routes[route_id] = entry
    first, last = price_period(inputs, weeks)
    return {
        "typical_fill_gal": params.typical_fill_gal, "price_basis": PRICE_BASIS_TEXT.format(weeks=weeks),
        "price_period_start": first.isoformat(), "price_period_end": last.isoformat(),
        "include_unverified_default": params.include_unverified_default,
        "default_route_id": default_route_id(routes), "rush_hour_note": rush_hour_note(params), "ranking_note": RANKING_NOTE,
        "worked_example": worked_example(prices, inputs, params, cfg), "routes": routes,
    }


def worked_example(prices: dict[str, float], inputs: Inputs, params: SavingsParams, cfg: dict) -> dict:
    """The Test route page's worked example: what a detour costs at a weekday time, and the price gap that pays for it.

    Uses the same cost function as everything else, at the median Tucson basis price and the typical fill, with no
    schedule risk (it is not a cost). Rounded once at the end.
    """
    minutes = float(config.get(cfg, "time_cost.worked_example_detour_minutes"))
    miles = float(config.get(cfg, "time_cost.worked_example_detour_miles"))
    when = str(config.get(cfg, "time_cost.worked_example_time"))
    hours, mins = (int(x) for x in when.split(":"))
    mult = multiplier_at_minute(hours * MINUTES_PER_HOUR + mins, params)  # the example is always a weekday
    south, west, north, east = (float(v) for v in config.get(cfg, "fetch.overpass.tucson_metro_bbox"))
    tucson = [p for sid, p in prices.items()
              if south <= inputs.stations[sid]["lat"] <= north and west <= inputs.stations[sid]["lon"] <= east]
    if not tucson:
        raise ValueError("The worked example needs basis prices for stations inside fetch.overpass.tucson_metro_bbox, but there are none.")
    price = median(tucson)
    mpg = float(config.get(cfg, "fleet.local_mpg"))
    parts = cost_parts(price, miles, minutes, mpg, params, mult)
    gallons = params.typical_fill_gal
    cpg = parts["total"] / gallons * 100.0
    first, last = price_period(inputs, price_basis_weeks(cfg))
    pieces = [("driver time", parts["time_usd"]), ("fuel", parts["fuel_burn_usd"]), ("wear", parts["wear_usd"])]
    named = ", ".join(f"${value:.2f} {label}" for label, value in pieces if value > 0)  # the parts shown are the computed fields
    basis = f"median Tucson posted price, {date_range_text(first, last)}"
    return {
        "detour_minutes": minutes, "detour_miles": miles, "time_local": when, "is_peak": is_peak_minute(hours * MINUTES_PER_HOUR + mins, params), "multiplier": mult,
        "effective_minutes": round(parts["minutes_eff"], 1), "time_cost_usd": round(parts["time_usd"], 2),
        "fuel_cost_usd": round(parts["fuel_burn_usd"], 2), "wear_cost_usd": round(parts["wear_usd"], 2),
        "total_usd": round(parts["total"], 2), "fill_gal": gallons,
        "price_per_gal": round(price, 3), "cpg_needed": round(cpg, 1),
        "price_period_start": first.isoformat(), "price_period_end": last.isoformat(), "price_basis": basis,
        "sentence": (f"For a {_trim(gallons, 0)}-gallon fill at ${price:.3f} a gallon ({basis}), a {_trim(minutes, 0)}-minute detour "
                     f"at {clock_text(when)} costs ${parts['total']:.2f} ({named}), "
                     f"so the station needs to be at least {cpg:.1f} cents a gallon cheaper to pay off."),
    }


# ---------------------------------------------------------------- break-even

def break_even_table(inputs: Inputs, params: SavingsParams, cfg: dict, local_route_ids: list[str]) -> dict[str, dict]:
    """Per station, the SPEC section 6 gap in cents a gallon, at the stop time of the median route's insertion.

    Uses the Tucson route whose detour cost to the station is the median (miles and minutes both come from that one route;
    with an even number of routes, the lower-middle one), priced at the planned weekday time of the stop it is inserted after.
    No schedule risk. The gap is the same detour cost net savings uses, over a typical fill, so cost(actual) = cost(alternative)
    exactly at the gap. Each entry is {"cpg", "route_id", "is_peak"}.

    Stations with no detour entry on any Tucson route are left out (the site shows no break-even).
    """
    prices = basis_prices(inputs, price_basis_weeks(cfg))
    mpg = float(config.get(cfg, "fleet.local_mpg"))
    gallons = params.typical_fill_gal
    out: dict[str, dict] = {}
    for sid, price in sorted(prices.items()):
        found = []  # (detour cost in dollars, route id, priced insertion) for each Tucson route with an entry
        for route_id in local_route_ids:
            options = inputs.detours.get((route_id, sid))
            if options:
                best, _ = pick_insertion(inputs, params, route_id, options, gallons, price, mpg)  # same insertion the option table shows
                found.append((best.total - gallons * price, route_id, best))
        if found:
            found.sort(key=lambda item: (item[0], item[1]))
            _, route_id, chosen = found[(len(found) - 1) // 2]  # lower-middle route when the count is even
            gap = gap_needed_per_gal(price, chosen.detour.extra_miles, chosen.detour.extra_minutes, mpg, gallons, params, chosen.mult)
            out[sid] = {"cpg": round(gap * 100.0, 1), "route_id": route_id, "is_peak": is_peak_minute(chosen.minute, params)}
    return out


__all__ = ["break_even_table", "compute_detours", "worked_example"]

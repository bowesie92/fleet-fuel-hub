"""Missed savings: fills where a reachable station would have cost less after detour costs.

In business terms: for every fuel card fill we ask "was there another station this truck could
have reached, whose total cost (fuel plus the detour to get there) was lower by more than the
minimum saving?" Only then do we count it. The saving is always cost(actual) minus cost(alt),
never a raw price difference, and it splits into three parts that always add up:

    net saving = price gap x gallons + detour cost at the station used - detour cost to the alternative

Two versions are produced: confirmed truck-access alternatives only (the default), and one that
also allows unverified stations (the site toggle). The detour-length limit applies to the
alternative, never to pricing the station the driver actually used.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from fleetfuel.analytics.net_savings import (
    Detour, Inputs, SavingsParams, best_feasible_insertion, best_insertion, is_peak, multiplier, stop_cost,
)

CENT = 2  # decimals of a dollar figure shown on the site


def fill_mult(fill: dict, params: SavingsParams) -> float:
    """The rush-hour multiplier at the fill's own local time."""
    return multiplier(fill["ts"], params)


def actual_cost(fill: dict, inputs: Inputs, params: SavingsParams) -> tuple[float, Detour] | None:
    """Total cost of the fill where it happened, with that station's detour. None when ref_detour has no entry for it.

    Every insertion point is allowed here: the detour limit is for alternatives, not for the station the driver used.
    """
    options = inputs.detours.get((fill["route_id"], fill["station_id"]))
    if not options:
        return None
    return best_insertion(options, fill["gallons"], fill["price_paid"], fill["mpg"], params, fill_mult(fill, params))


def alternative_cost(
    fill: dict, alt_id: str, inputs: Inputs, params: SavingsParams, price_cut_cpg: float = 0.0
) -> tuple[float, Detour, float] | None:
    """Total cost of the same fill at another station: (cost, detour used, price per gallon).

    The price is the posted price that day, net of the station's partner discount, and net of an
    extra `price_cut_cpg` (the partnership scout's what-if discount). None when the alternative
    has no detour entry for this route, no price that day, or cannot be reached feasibly. Any feasible insertion point counts; the cheapest feasible one is used.
    """
    options = inputs.detours.get((fill["route_id"], alt_id))
    price = inputs.net_posted(alt_id, fill["date"])
    if not options or price is None:
        return None
    price -= price_cut_cpg / 100.0
    base_minutes = inputs.base_minutes_for(fill["route_id"])
    found = best_feasible_insertion(options, fill["gallons"], price, fill["mpg"], fill["tank_gal"], base_minutes, params, fill_mult(fill, params))
    if found is None:
        return None
    return found[0], found[1], price


def best_alternative(fill: dict, actual_total: float, inputs: Inputs, params: SavingsParams, include_unverified: bool) -> dict | None:
    """The alternative that saves the most after detour costs, if it beats the minimum saving."""
    best: dict | None = None
    for alt_id, station in sorted(inputs.stations.items()):
        if alt_id == fill["station_id"]:
            continue
        if station["truck_access"] != "confirmed" and not include_unverified:
            continue
        found = alternative_cost(fill, alt_id, inputs, params)
        if found is None:
            continue
        cost, detour, price = found
        saving = actual_total - cost
        if saving > params.min_saving_threshold_usd and (best is None or saving > best["saving"]):
            best = {"alt_station_id": alt_id, "alt_cost": cost, "alt_detour": detour, "alt_price": price,
                    "saving": saving, "alt_truck_access": station["truck_access"]}
    return best


def breakdown(fill: dict, actual_total: float, alt: dict) -> dict:
    """The three parts of one missed saving, unrounded: price gap x gallons, detour cost at the station used, detour cost to the alternative.

    net = price_gap + actual_detour - alt_detour holds exactly, because cost = gallons x price + detour cost on both sides.
    """
    gallons = fill["gallons"]
    return {
        "price_gap_usd": (fill["price_paid"] - alt["alt_price"]) * gallons,
        "actual_detour_cost_usd": actual_total - gallons * fill["price_paid"],
        "alt_detour_cost_usd": alt["alt_cost"] - gallons * alt["alt_price"],
    }


def rounded_parts(net: float, parts: dict) -> dict:
    """The breakdown in whole cents. The alternative's detour cost is the cent figure that makes the displayed parts add to the displayed saving, so it can differ by one cent from its own rounded value (rounding only, never a different cost)."""
    gap = round(parts["price_gap_usd"], CENT)
    actual = round(parts["actual_detour_cost_usd"], CENT)
    return {"price_gap_usd": gap, "actual_detour_cost_usd": actual, "alt_detour_cost_usd": round(gap + actual - net, CENT)}


MILE_DECIMALS = 1  # decimals on a mile figure in the why line
EXPORTED_MILE_DECIMALS = 2  # decimals on actual_extra_miles and alt_extra_miles in missed.json (export.public_rows)
SHORT_SAVING_MILES = 10 ** -MILE_DECIMALS  # below this, the saving is in minutes and no mile figure is shown
AN_WORDS = {8, 11, 18}  # whole-mile numbers read aloud with a leading vowel sound ("an 8-mile", "an 11-mile", "an 18-mile")


def why_reason(fill: dict, alt: dict, parts: dict) -> str:
    """Why the lower-cost option wins, on unrounded values: "lower price", "shorter detour" or "both"."""
    lower_price = alt["alt_price"] < fill["price_paid"]
    shorter_detour = parts["alt_detour_cost_usd"] < parts["actual_detour_cost_usd"]
    if lower_price and shorter_detour:
        return "both"
    return "lower price" if lower_price else "shorter detour"


def miles_text(miles: float) -> str:
    """A mile figure with one decimal and a whole number written without ".0" (5.0 becomes "5")."""
    text = f"{miles:.{MILE_DECIMALS}f}"
    return text[: -(MILE_DECIMALS + 1)] if text.endswith("." + "0" * MILE_DECIMALS) else text


def article(miles_label: str) -> str:
    """"an" before 8, 11, 18 and the eighties (an 8.2-mile, an 11-mile), otherwise "a"."""
    whole = int(float(miles_label))
    return "an" if whole in AN_WORDS or str(whole).startswith("8") else "a"


def saved_detour_phrase(saved_miles: float) -> str:
    """The shorter detour in words: "saves a 5-mile detour", or "a shorter detour" when under a tenth of a mile is saved."""
    if saved_miles < SHORT_SAVING_MILES:
        return "a shorter detour"
    label = miles_text(saved_miles)
    return f"saves {article(label)} {label}-mile detour"


def panel_miles(miles: float) -> Decimal:
    """A detour's miles exactly as the comparison panel prints them.

    The row exports miles at 2 decimals, and the page shows them at 1 decimal with JavaScript's toFixed,
    which rounds the exact binary value half up. This copies both steps.
    """
    exported = round(miles, EXPORTED_MILE_DECIMALS)
    return Decimal(exported).quantize(Decimal(1).scaleb(-MILE_DECIMALS), rounding=ROUND_HALF_UP)


def shown_miles_saved(actual_detour: Detour, alt: dict) -> float:
    """Miles saved as the panel prints them, so the why line always matches the two mile figures above it."""
    return float(panel_miles(actual_detour.extra_miles) - panel_miles(alt["alt_detour"].extra_miles))


def extra_detour_phrase(extra_miles: float) -> str:
    """The longer detour in words: "the extra 0.3-mile detour", or "the slightly longer detour" when no more miles."""
    if extra_miles < SHORT_SAVING_MILES:
        return "the slightly longer detour"
    return f"the extra {miles_text(extra_miles)}-mile detour"


def why_line(reason: str, fill: dict, alt: dict, actual_detour: Detour) -> str:
    """One plain-English sentence saying why the lower-cost option wins (contract 13.1)."""
    saved_miles = shown_miles_saved(actual_detour, alt)
    if reason == "both":
        return f"Lower pump price and {saved_detour_phrase(saved_miles)}."
    if reason == "lower price":
        return f"Lower pump price, worth {extra_detour_phrase(-saved_miles)}."
    opening = "Higher pump price, but" if alt["alt_price"] > fill["price_paid"] else "Same pump price, and"
    return f"{opening} {saved_detour_phrase(saved_miles)}."


def missed_row(fill: dict, actual_total: float, actual_detour: Detour, alt: dict, params: SavingsParams) -> dict:
    """One missed-savings row (contract 11.5), with the saving broken into its parts at the fill's own time."""
    net = round(alt["saving"], CENT)
    parts = breakdown(fill, actual_total, alt)
    reason = why_reason(fill, alt, parts)
    return {
        "txn_id": fill["txn_id"], "date": fill["date"], "truck_id": fill["truck_id"], "route_id": fill["route_id"],
        "actual_station_id": fill["station_id"], "alt_station_id": alt["alt_station_id"],
        "gallons": fill["gallons"], "actual_price_per_gal": fill["price_paid"], "alt_price_per_gal": alt["alt_price"],
        "actual_extra_miles": actual_detour.extra_miles, "actual_extra_minutes": actual_detour.extra_minutes,
        "alt_extra_miles": alt["alt_detour"].extra_miles, "alt_extra_minutes": alt["alt_detour"].extra_minutes,
        "actual_insert_after_stop": actual_detour.insert_after_stop, "alt_insert_after_stop": alt["alt_detour"].insert_after_stop,
        "is_peak": is_peak(fill["ts"], params), "multiplier": fill_mult(fill, params),
        **rounded_parts(net, parts),
        "net_savings_usd": net, "alt_truck_access": alt["alt_truck_access"],
        "why": reason, "why_line": why_line(reason, fill, alt, actual_detour),
    }


def find_missed(inputs: Inputs, params: SavingsParams, include_unverified: bool) -> list[dict]:
    """Every fill with a feasible alternative that is cheaper by more than the threshold, after detour costs.

    Returns rows sorted by net savings, largest first. Fills whose own station has no ref_detour
    entry cannot be priced fairly, so they are left out (counted in `inputs.skipped`).
    """
    rows: list[dict] = []
    unpriced = 0
    for fill in inputs.fills:
        actual = actual_cost(fill, inputs, params)
        if actual is None:
            unpriced += 1
            continue
        total, actual_detour = actual
        alt = best_alternative(fill, total, inputs, params, include_unverified)
        if alt is not None:
            rows.append(missed_row(fill, total, actual_detour, alt, params))
    inputs.skipped["actual_station_has_no_detour"] = unpriced
    rows.sort(key=lambda r: (-r["net_savings_usd"], r["txn_id"]))
    return rows


# Re-exported so tests and callers can price a single stop with the same formula.
__all__ = ["actual_cost", "alternative_cost", "best_alternative", "breakdown", "find_missed", "stop_cost"]

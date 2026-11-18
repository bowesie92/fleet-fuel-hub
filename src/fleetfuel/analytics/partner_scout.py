"""Tucson partnership scout: which station is worth signing as a fuel partner, and for how much a year.

In business terms: pick a Tucson-area station that is not yet a partner and imagine it offered
the fleet X cents a gallon off. Two groups of fills would benefit. (1) Fills already made there
save X cents on every gallon. (2) Fills made elsewhere move there when, after the extra miles and
driver time of the detour, the stop is cheaper by more than the minimum saving. Shifted savings
always use the SPEC section 6 cost formula, so no raw price difference is ever counted. Each fill is credited only with the saving the deal creates (SPEC section 6, R1), at most the discount on its gallons, scaled from the period length to a year. This is a synthetic scenario.
"""

from __future__ import annotations

from fleetfuel import config
from fleetfuel.analytics.missed import actual_cost, alternative_cost
from fleetfuel.analytics.net_savings import Inputs, SavingsParams

DAYS_PER_YEAR = 365  # a calendar fact, not an assumption


def discount_steps(cfg: dict) -> list[int]:
    """The slider values from config: min, max and step."""
    low = int(config.get(cfg, "partner_scout.discount_min_cpg"))
    high = int(config.get(cfg, "partner_scout.discount_max_cpg"))
    step = int(config.get(cfg, "partner_scout.discount_step_cpg"))
    return list(range(low, high + 1, step))


def candidate_ids(inputs: Inputs, cfg: dict) -> list[str]:
    """Stations inside the Tucson metro box that are truck-confirmed and not already partners."""
    south, west, north, east = (float(v) for v in config.get(cfg, "fetch.overpass.tucson_metro_bbox"))
    found = []
    for sid, s in sorted(inputs.stations.items()):
        inside = south <= s["lat"] <= north and west <= s["lon"] <= east
        if inside and s["truck_access"] == "confirmed" and not s["is_partner"]:
            found.append(sid)
    return found


def fill_gap(fill: dict, cand_id: str, actual_total: float, cut_cpg: float, inputs: Inputs, params: SavingsParams) -> float | None:
    """gap_X: cost of the fill as made minus its cost at the candidate with a `cut_cpg` discount (detours included); None if infeasible."""
    found = alternative_cost(fill, cand_id, inputs, params, price_cut_cpg=cut_cpg)
    return None if found is None else actual_total - found[0]


def shift_gap(fill: dict, cand_id: str, actual_total: float, cut_cpg: float, inputs: Inputs, params: SavingsParams) -> float | None:
    """The fill's gap if it would move to the candidate at `cut_cpg` (feasible and gap above the minimum saving); otherwise None."""
    gap = fill_gap(fill, cand_id, actual_total, cut_cpg, inputs, params)
    return gap if gap is not None and gap > params.min_saving_threshold_usd else None


def deal_value(gap_x: float, gap_0: float | None, gallons: float, cut_cpg: float) -> float:
    """SPEC section 6 (R1): what the deal itself creates for a fill that moves.

    The gap that already existed at posted prices is not the deal's doing, and the deal can never
    be credited with more than the discount on the gallons it captures.
    """
    return min(gap_x - max(gap_0 if gap_0 is not None else 0.0, 0.0), gallons * cut_cpg / 100.0)


def curve_point(cand_id: str, cut_cpg: int, priced_fills: list[tuple[dict, float]], inputs: Inputs, params: SavingsParams) -> tuple[dict, float, float, float]:
    """One point on a station's curve at a `cut_cpg` discount over the whole period.

    Returns (point, period deal value, period gap of fills that move, exact captured gallons). The deal value is the existing
    fills' discount plus each moved fill's deal value; the gap total at X = 0 is what the fleet could
    already save at posted prices. The point's annual figure is filled in by `compute_scout`.
    """
    existing_fills, existing_gal, deal_usd = 0, 0.0, 0.0
    shifted_fills, shifted_gal, gap_usd = 0, 0.0, 0.0
    for fill in inputs.fills:  # the discount applies at the pump, so every fill at the candidate counts
        if fill["station_id"] == cand_id:
            existing_fills += 1
            existing_gal += fill["gallons"]
            deal_usd += fill["gallons"] * cut_cpg / 100.0
    for fill, actual_total in priced_fills:
        if fill["station_id"] == cand_id:
            continue
        gap = shift_gap(fill, cand_id, actual_total, cut_cpg, inputs, params)
        if gap is not None:
            shifted_fills += 1
            shifted_gal += fill["gallons"]
            gap_usd += gap
            gap_0 = fill_gap(fill, cand_id, actual_total, 0.0, inputs, params)
            deal_usd += deal_value(gap, gap_0, fill["gallons"], cut_cpg)
    scale = DAYS_PER_YEAR / inputs.period_days()
    point = {
        "discount_cpg": cut_cpg,
        "fills_existing": existing_fills, "gallons_existing": round(existing_gal, 1),
        "fills_shifted": shifted_fills, "gallons_shifted": round(shifted_gal, 1),
        "gallons_per_month": round((existing_gal + shifted_gal) * scale / 12.0, 1),
        "annual_net_savings_usd": 0.0,
    }
    return point, deal_usd, gap_usd, existing_gal + shifted_gal


def compute_scout(inputs: Inputs, params: SavingsParams, cfg: dict) -> list[dict]:
    """Curves for every candidate station, ranked by annual net savings at the default discount, largest first."""
    steps = discount_steps(cfg)
    default = int(config.get(cfg, "partner_scout.default_discount_cpg"))
    if default not in steps:
        raise ValueError(f"partner_scout.default_discount_cpg ({default}) is not one of the slider steps {steps[0]}..{steps[-1]}.")
    priced = []
    for fill in inputs.fills:
        actual = actual_cost(fill, inputs, params)
        if actual is not None:
            priced.append((fill, actual[0]))
    out = []
    for cand_id in candidate_ids(inputs, cfg):
        scale = DAYS_PER_YEAR / inputs.period_days()
        points = [curve_point(cand_id, x, priced, inputs, params) for x in steps]
        without_deal = curve_point(cand_id, 0, priced, inputs, params)[2]  # shifted savings at posted prices: already missed savings
        curve = []
        for point, deal_usd, _, captured_gal in points:
            point["net_savings_cpg_captured"] = round(deal_usd / captured_gal * 100, 2) if captured_gal else 0.0  # exact gallons: never above the discount
            point["annual_net_savings_usd"] = round(deal_usd * scale, 2)  # only what the deal creates
            curve.append(point)
        out.append({"station_id": cand_id, "annual_savings_without_deal_usd": round(without_deal * scale, 2), "curve": curve})
    def at_default(entry: dict) -> float:
        return next(p["annual_net_savings_usd"] for p in entry["curve"] if p["discount_cpg"] == default)
    out.sort(key=lambda e: (-at_default(e), e["station_id"]))
    return out

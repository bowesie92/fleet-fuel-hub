"""Fixture fills F1 to F3 on the made-up mini hub, plus the scout and price history. These are NOT the hand checks.

The real hand-checked fills (FC2028603, FC4274959, FC0528809, docs/hand-checks.md) are in
tests/test_analytics_handchecked.py. The fills here are invented to test the machinery on a tiny database.

Net savings on the mini hub: three missed-savings fills and the partnership scout, to the cent.

Every expected number below was worked out on paper first (the arithmetic is in the comments).
The tiny hub comes from tests/fixtures/analytics/hubkit.py. Assumptions are pinned there:
driver $38.00 an hour, wear $0.25 a mile, minimum saving $5.00, reserve 25 gal, fills to 95%
of a 120 gal tank, trucks at 6.0 mpg.

The formula (SPEC section 6, contract v1.11), with G gallons, price p, detour miles m, minutes t and the
rush-hour multiplier k (1.17 inside the peak windows on a weekday, otherwise 1):
    cost = G*p + (m/6.0)*p + (k*t/60)*38.00 + m*0.25
There is no schedule-risk term (it only ranks Test route stops). The detour limit is 15 effective minutes for
an alternative; the station actually used is always priced, however long its detour.
"""

import copy
import importlib.util
from pathlib import Path

import duckdb
import pytest

from fleetfuel.analytics import partner_scout, price_history
from fleetfuel.analytics.missed import find_missed
from fleetfuel.analytics.net_savings import (
    Detour, SavingsParams, fuel_on_board, gap_needed_per_gal, is_feasible, load_inputs, stop_cost,
)

OFF_PEAK = 1.0  # the rush-hour multiplier m(t) of an off-peak fill
KIT_PATH = Path(__file__).parent / "fixtures" / "analytics" / "hubkit.py"
spec = importlib.util.spec_from_file_location("analytics_hubkit", KIT_PATH)
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)


@pytest.fixture
def world(tmp_path):
    """The cfg, an open connection to the mini hub, the savings params and the loaded inputs."""
    cfg = kit.test_cfg()
    path = kit.build_mini_hub(tmp_path / "mini.duckdb")
    con = duckdb.connect(str(path))
    params = SavingsParams.from_config(cfg)
    inputs = load_inputs(con, cfg)
    yield cfg, con, params, inputs
    con.close()


def by_txn(rows):
    return {r["txn_id"]: r for r in rows}


# ------------------------------------------------------------------ the formula itself

def test_stop_cost_matches_the_spec_formula(world):
    _, _, params, _ = world
    # F1 at the actual station A: G=80, p=4.000, detour 1.0 mi and 3 min, mpg 6.0
    #   80*4.000        = 320.000000
    #   (1.0/6)*4.000   =   0.666667   fuel burned on the detour
    #   (3/60)*38       =   1.900000   driver time
    #   1.0*0.25        =   0.250000   wear and tear
    #   total           = 322.816667
    assert stop_cost(80, 4.000, 1.0, 3.0, 6.0, params, OFF_PEAK) == pytest.approx(322.816667, abs=1e-6)


def test_variable_cost_term_is_optional(world):
    cfg, _, _, _ = world
    cfg = copy.deepcopy(cfg)
    cfg["savings"]["include_variable_cost"] = False
    params = SavingsParams.from_config(cfg)
    # same as above without the 0.25 wear term: 322.816667 - 0.25 = 322.566667
    assert stop_cost(80, 4.000, 1.0, 3.0, 6.0, params, OFF_PEAK) == pytest.approx(322.566667, abs=1e-6)


def test_break_even_gap_includes_the_wear_term(world):
    _, _, params, _ = world
    # Detour 2.0 mi and 4 min at p=3.85, G=90, mpg 6.0:
    #   (2.0/6)*3.85 = 1.283333 fuel burned ; (4/60)*38 = 2.533333 driver ; 2.0*0.25 = 0.500000 wear
    #   sum 4.316667 ; / 90 = 0.047963 dollars
    # So the station must be at least 4.8 cents a gallon cheaper to pay for the detour.
    gap = gap_needed_per_gal(3.85, 2.0, 4.0, 6.0, 90, params, OFF_PEAK)
    assert gap == pytest.approx(0.0479630, abs=1e-6)
    assert round(gap * 100, 1) == 4.8


def test_break_even_gap_drops_wear_when_it_is_switched_off(world):
    cfg, _, _, _ = world
    cfg = copy.deepcopy(cfg)
    cfg["savings"]["include_variable_cost"] = False
    params = SavingsParams.from_config(cfg)
    # (1.283333 + 2.533333) / 90 = 0.042407 dollars, 4.2 cents
    assert gap_needed_per_gal(3.85, 2.0, 4.0, 6.0, 90, params, OFF_PEAK) == pytest.approx(0.0424074, abs=1e-6)


def test_at_the_break_even_gap_both_stops_cost_the_same_to_the_cent(world):
    _, _, params, _ = world
    # Actual station: 90 gal, no detour at all, at price p + gap. Alternative: price p, detour 2.0 mi / 4 min.
    # gap = 0.047963 (above); alt price 3.850, actual price 3.897963.
    #   cost(actual) = 90*3.897963               = 350.816667
    #   cost(alt)    = 90*3.85 = 346.5 + 4.316667 = 350.816667
    p, gap = 3.85, gap_needed_per_gal(3.85, 2.0, 4.0, 6.0, 90, params, OFF_PEAK)
    actual = stop_cost(90, p + gap, 0.0, 0.0, 6.0, params, OFF_PEAK)
    alternative = stop_cost(90, p, 2.0, 4.0, 6.0, params, OFF_PEAK)
    assert round(actual, 2) == round(alternative, 2) == 350.82
    assert actual == pytest.approx(alternative, abs=1e-9)


def test_feasibility_needs_fuel_and_time(world):
    _, _, params, _ = world
    # On board = 0.95*120 - G. A 110 gal fill leaves 114 - 110 = 4 gal: below the 25 gal reserve.
    assert fuel_on_board(120, 110, params) == pytest.approx(4.0)
    assert not is_feasible(120, 110, 6.0, Detour(1, 2.0, 6.0), 300, params, OFF_PEAK)
    # An 80 gal fill leaves 34 gal; reaching a 2.0 mi detour burns 2.0/6 = 0.33 gal; 33.67 >= 25.
    assert is_feasible(120, 80, 6.0, Detour(1, 2.0, 6.0), 300, params, OFF_PEAK)
    # A route day already at 839 minutes cannot take a 6 minute stop (limit 840).
    assert not is_feasible(120, 80, 6.0, Detour(1, 2.0, 6.0), 839, params, OFF_PEAK)


# ------------------------------------------------------------------ three hand-checked missed-savings fills

def test_three_fills_match_hand_calculation_to_the_cent(world):
    _, _, params, inputs = world
    rows = by_txn(find_missed(inputs, params, include_unverified=False))

    # F1 (FC0000001): G=80 at A, paid 4.000. A detour 1.0 mi / 3 min.
    #   cost(A) = 322.816667 (above)
    #   Alternative B: posted 3.900 less its 10 cent partner discount = 3.800; detour 2.0 mi / 6 min.
    #   cost(B) = 80*3.800 = 304.000000 + (2.0/6)*3.800 = 1.266667 + (6/60)*38 = 3.800000 + 2.0*0.25 = 0.500000
    #           = 309.566667
    #   net saving = 322.816667 - 309.566667 = 13.25
    #   (a raw price difference would have said 0.200 * 80 = 16.00; that number is never reported)
    assert rows["FC0000001"]["net_savings_usd"] == 13.25
    assert rows["FC0000001"]["alt_station_id"] == "STN-002"
    assert rows["FC0000001"]["alt_price_per_gal"] == pytest.approx(3.800)
    # The same saving in its parts (contract 11.5), all at 10:30 (off-peak, k = 1, so the 1.17 index does not apply):
    #   price gap x gallons = (4.000 - 3.800) * 80 = 16.00
    #   detour cost at the station used  = 322.816667 - 80*4.000 = 2.816667 -> 2.82
    #   detour cost to the alternative   = 309.566667 - 80*3.800 = 5.566667 -> 5.57   (6 minutes, inside the 15 minute limit)
    #   16.00 + 2.82 - 5.57 = 13.25
    r1 = rows["FC0000001"]
    assert (r1["price_gap_usd"], r1["actual_detour_cost_usd"], r1["alt_detour_cost_usd"]) == (16.00, 2.82, 5.57)
    assert (r1["actual_extra_miles"], r1["actual_extra_minutes"], r1["actual_insert_after_stop"]) == (1.0, 3.0, 1)
    assert (r1["alt_extra_miles"], r1["alt_extra_minutes"], r1["alt_insert_after_stop"]) == (2.0, 6.0, 1)
    assert r1["is_peak"] is False and r1["multiplier"] == 1.0

    # F2 (FC0000002): G=80 at A, paid 3.950, same detours.
    #   cost(A) = 316.000000 + (1.0/6)*3.950 = 0.658333 + 1.900000 + 0.250000 = 318.808333
    #   cost(B) = 309.566667 (B is 3.800 net again)
    #   net saving = 318.808333 - 309.566667 = 9.241667 -> 9.24
    assert rows["FC0000002"]["net_savings_usd"] == 9.24
    #   parts: price gap (3.950 - 3.800) * 80 = 12.00 ; used-station detour 318.808333 - 316.00 = 2.808333 -> 2.81 ;
    #   alternative detour 5.566667 -> 5.57 ; 12.00 + 2.81 - 5.57 = 9.24
    r2 = rows["FC0000002"]
    assert (r2["price_gap_usd"], r2["actual_detour_cost_usd"], r2["alt_detour_cost_usd"]) == (12.00, 2.81, 5.57)

    # F3 (FC0000003) on route R03: G=60 at A, paid 4.100. A detour 0.5 mi / 2 min.
    #   cost(A) = 60*4.100 = 246.000000 + (0.5/6)*4.100 = 0.341667 + (2/60)*38 = 1.266667 + 0.5*0.25 = 0.125000
    #           = 247.733333
    #   Alternative B: posted 3.950 less 0.100 = 3.850; detour 1.5 mi / 5 min.
    #   cost(B) = 60*3.850 = 231.000000 + (1.5/6)*3.850 = 0.962500 + (5/60)*38 = 3.166667 + 1.5*0.25 = 0.375000
    #           = 235.504167
    #   net saving = 247.733333 - 235.504167 = 12.229167 -> 12.23
    assert rows["FC0000003"]["net_savings_usd"] == 12.23
    #   parts: price gap (4.100 - 3.850) * 60 = 15.00 ; used-station detour 247.733333 - 246.00 = 1.733333 -> 1.73 ;
    #   alternative detour 235.504167 - 231.00 = 4.504167 -> 4.50 ; 15.00 + 1.73 - 4.50 = 12.23
    r3 = rows["FC0000003"]
    assert (r3["price_gap_usd"], r3["actual_detour_cost_usd"], r3["alt_detour_cost_usd"]) == (15.00, 1.73, 4.50)

    # Only these three count; the total is the sum of the rounded rows: 13.25 + 9.24 + 12.23 = 34.72
    assert set(rows) == {"FC0000001", "FC0000002", "FC0000003"}
    assert round(sum(r["net_savings_usd"] for r in rows.values()), 2) == 34.72


def test_cheaper_price_alone_is_not_a_missed_saving(world):
    _, _, params, inputs = world
    rows = by_txn(find_missed(inputs, params, include_unverified=False))
    # F4: B is cheaper by 0.400 a gallon, but the truck has only 4 gal on board (reserve is 25): not feasible.
    assert "FC0000004" not in rows
    # F5: G=50 at A, paid 3.920. cost(A) = 196.000 + (1/6)*3.92 = 0.653333 + 1.9 + 0.25 = 198.803333
    #     cost(B) = 50*3.800 = 190.000 + 1.266667 + 3.800 + 0.500 = 195.566667
    #     net saving = 3.236667, under the 5.00 minimum. (The raw price gap, 0.120 * 50 = 6.00, would have passed.)
    assert "FC0000005" not in rows


def test_unverified_toggle_changes_the_alternative(world):
    _, _, params, inputs = world
    confirmed = by_txn(find_missed(inputs, params, include_unverified=False))
    incl = by_txn(find_missed(inputs, params, include_unverified=True))
    # F3 with unverified station C: posted 3.700 (not a partner); detour 1.0 mi / 3 min.
    #   cost(C) = 60*3.700 = 222.000000 + (1.0/6)*3.700 = 0.616667 + 1.900000 + 0.250000 = 224.766667
    #   net saving = 247.733333 - 224.766667 = 22.966667 -> 22.97
    assert incl["FC0000003"]["net_savings_usd"] == 22.97
    assert incl["FC0000003"]["alt_station_id"] == "STN-003"
    assert incl["FC0000003"]["alt_truck_access"] == "unverified"
    assert confirmed["FC0000003"]["alt_truck_access"] == "confirmed"
    # F1 and F2 are unchanged (C is not cheaper enough there). Total with unverified: 13.25 + 9.24 + 22.97 = 45.46
    assert incl["FC0000001"]["net_savings_usd"] == 13.25
    assert round(sum(r["net_savings_usd"] for r in incl.values()), 2) == 45.46


def test_rows_sorted_largest_first_and_unpriced_fills_are_counted(world):
    _, con, params, inputs = world
    rows = find_missed(inputs, params, include_unverified=False)
    assert [r["net_savings_usd"] for r in rows] == sorted((r["net_savings_usd"] for r in rows), reverse=True)
    # Remove the detour entry of the station where F1 happened: the fill cannot be priced fairly, so it is skipped.
    con.execute("DELETE FROM ref_detour WHERE route_id = 'TUC-R01' AND station_id = 'STN-001'")
    again = by_txn(find_missed(load_inputs(con, kit.test_cfg()), params, include_unverified=False))
    assert "FC0000001" not in again


# ------------------------------------------------------------------ partnership scout

def test_scout_candidates_are_tucson_confirmed_non_partners(world):
    cfg, _, _, inputs = world
    # A and S qualify; B is a partner; C is unverified.
    assert partner_scout.candidate_ids(inputs, cfg) == ["STN-001", "STN-004"]


def test_scout_existing_and_shifted_fills_by_hand(world):
    cfg, _, params, inputs = world
    scout = {e["station_id"]: e for e in partner_scout.compute_scout(inputs, params, cfg)}
    curve = {p["discount_cpg"]: p for p in scout["STN-004"]["curve"]}

    # Candidate S = STN-004, discount X = 10 cents. The period is 365 days, so the annual factor is 1.
    # Existing fill X1: G=100 already at S. Saves G*X/100 = 100*10/100 = 10.00 (no detour: it was already there).
    # Shifted fill X2: G=80 at A, paid 4.000, A detour 1.0 mi / 3 min on R02.
    #   cost(A) = 322.816667
    #   S posted 3.950 less X 0.100 = 3.850; S detour 2.0 mi / 4 min.
    #   cost(S) = 80*3.850 = 308.000000 + (2.0/6)*3.850 = 1.283333 + (4/60)*38 = 2.533333 + 2.0*0.25 = 0.500000
    #           = 312.316667
    #   gap_10 = 322.816667 - 312.316667 = 10.50 (over the 5.00 minimum, feasible: 34 gal on board)
    #   gap_0 = 322.816667 - 320.350000 = 2.466667 (positive, under the minimum: a pre-existing gap, not the deal's)
    #   deal value (SPEC section 6, R1) = min(10.50 - 2.466667, 80*10/100 = 8.00) = min(8.033333, 8.00) = 8.00
    #   (raw price difference 4.000 - 3.850 = 0.15 * 80 = 12.00 is never used)
    p10 = curve[10]
    assert p10["fills_existing"] == 1 and p10["gallons_existing"] == 100.0
    assert p10["fills_shifted"] == 1 and p10["gallons_shifted"] == 80.0
    assert p10["annual_net_savings_usd"] == 18.00           # 10.00 + 8.00
    assert p10["gallons_per_month"] == 15.0                  # (100 + 80) / 12

    # X = 1 cent: S at 3.940: cost(S) = 315.200000 + (2.0/6)*3.940 = 1.313333 + 2.533333 + 0.500000 = 319.546667
    #   saving = 322.816667 - 319.546667 = 3.27, under the 5.00 minimum, so X2 does not shift. Existing: 100*1/100 = 1.00.
    p1 = curve[1]
    assert p1["fills_shifted"] == 0 and p1["annual_net_savings_usd"] == 1.00

    # X = 0: S at 3.950 gives cost(S) = 320.350000, saving 2.466667 (under 5.00); existing fills save nothing.
    p0 = curve[0]
    assert p0["fills_shifted"] == 0 and p0["annual_net_savings_usd"] == 0.0


def test_scout_counts_only_what_the_deal_creates(world):
    cfg, con, params, _ = world
    # Make X2 (80 gal at A) cost more: paid 4.100 instead of 4.000.
    #   cost(A) = 80*4.100 = 328.000000 + (1.0/6)*4.100 = 0.683333 + 1.900000 + 0.250000 = 330.833333
    # With no discount, S at its posted 3.950: cost(S) = 316.000000 + (2.0/6)*3.950 = 1.316667 + 2.533333 + 0.5 = 320.350000
    #   saving = 330.833333 - 320.350000 = 10.483333 (over 5.00): X2 would already move at posted prices.
    #   That is a missed saving, not deal value: annual_savings_without_deal_usd = 10.48 (365 day period).
    # At X = 10: S at 3.850, cost(S) = 312.316667, saving = 330.833333 - 312.316667 = 18.516667.
    #   gap_0 = 10.483333 (above the minimum, so X2 already moved at X = 0); gap_10 = 18.516667
    #   X2 deal value = min(18.516667 - 10.483333 = 8.033333, 80*10/100 = 8.00) = 8.00
    #   existing X1 = 100*10/100 = 10.00 ; deal-created total = 10.00 + 8.00 = 18.00
    con.execute("UPDATE fact_fuel_txn SET price_per_gal = 4.1 WHERE txn_id = 'FC0000007'")
    scout = {e["station_id"]: e for e in partner_scout.compute_scout(load_inputs(con, cfg), params, cfg)}
    s = scout["STN-004"]
    curve = {p["discount_cpg"]: p for p in s["curve"]}
    assert s["annual_savings_without_deal_usd"] == 10.48
    assert curve[0]["annual_net_savings_usd"] == 0.0                 # nothing is created by a zero discount
    assert curve[10]["annual_net_savings_usd"] == 18.00
    assert curve[10]["gallons_per_month"] == 15.0                    # volume to offer: (100 + 80) / 12, the total at X


def test_scout_scales_a_short_period_to_a_year(world):
    cfg, con, params, _ = world
    short = copy.deepcopy(cfg)
    short["period"]["end_date"] = "2026-04-30"        # 2025-11-01 to 2026-04-30 is 181 days
    inputs = load_inputs(con, short)
    scout = {e["station_id"]: e for e in partner_scout.compute_scout(inputs, params, short)}
    p10 = {p["discount_cpg"]: p for p in scout["STN-004"]["curve"]}[10]
    # (10.00 + 8.00) * 365 / 181 = 18.00 * 2.016575 = 36.30 ; gallons a month = 180 * (365/181) / 12 = 30.2
    assert p10["annual_net_savings_usd"] == 36.30
    assert p10["gallons_per_month"] == 30.2


def test_scout_steps_and_default_come_from_config(world):
    cfg, _, params, inputs = world
    steps = partner_scout.discount_steps(cfg)
    assert steps[0] == cfg["partner_scout"]["discount_min_cpg"] and steps[-1] == cfg["partner_scout"]["discount_max_cpg"]
    assert cfg["partner_scout"]["default_discount_cpg"] in steps
    ranked = partner_scout.compute_scout(inputs, params, cfg)
    assert all([p["discount_cpg"] for p in e["curve"]] == steps for e in ranked)
    default = cfg["partner_scout"]["default_discount_cpg"]
    at_default = [next(p for p in e["curve"] if p["discount_cpg"] == default)["annual_net_savings_usd"] for e in ranked]
    assert at_default == sorted(at_default, reverse=True)


# ------------------------------------------------------------------ price history

def test_price_history_weekly_values(world):
    cfg, con, _, _ = world
    weekly = price_history.station_weekly(con, cfg)
    a = weekly[(weekly.station_id == "STN-001") & (weekly.week_start == "2026-01-05")].iloc[0]
    # Week of 2026-01-05, station A, fills F1 (80 gal at 4.000), F2 (80 gal at 3.950), F4 (110 at 4.200), F5 (50 at 3.920)
    # all fall on 01-05 to 01-09:  spend = 320.00 + 316.00 + 462.00 + 196.00 = 1294.00 ; gallons = 320
    #   gallon-weighted average = 1294.00 / 320 = 4.04375
    assert a["gallons"] == pytest.approx(320.0)
    assert a["avg_price_paid"] == pytest.approx(4.04375)
    # Weeks without fills have no price paid (null), not zero.
    empty = weekly[(weekly.station_id == "STN-002") & (weekly.week_start == "2026-01-05")].iloc[0]
    assert empty["gallons"] == 0.0 and empty["avg_price_paid"] != empty["avg_price_paid"]


# ------------------------------------------------------------------ the new rules, hand-checked (contract v1.11)

def test_a_rush_hour_fill_uses_the_1_17_index_on_both_detours(world):
    _, con, params, _ = world
    # F2 moved to Tuesday 2026-01-06 at 5 pm (inside 16:00-19:00): k = 1.17 on both detours.
    #   Used station A, 1.0 mi / 3 min: effective minutes 3 * 1.17 = 3.51 ; driver (3.51/60)*38 = 2.223000
    #     fuel (1.0/6)*3.950 = 0.658333 ; wear 1.0*0.25 = 0.250000 ; detour 3.131333 ; cost(A) = 80*3.950 = 316.00 + 3.131333 = 319.131333
    #   Alternative B (3.800), 2.0 mi / 6 min: effective 6 * 1.17 = 7.02 (under the 15 limit) ; driver (7.02/60)*38 = 4.446000
    #     fuel (2.0/6)*3.800 = 1.266667 ; wear 2.0*0.25 = 0.500000 ; detour 6.212667 ; cost(B) = 304.00 + 6.212667 = 310.212667
    #   net saving = 319.131333 - 310.212667 = 8.918667 -> 8.92
    #   parts: price gap 12.00 + 3.131333 (3.13) - 6.212667 (6.21) = 8.918667 ; displayed 12.00 + 3.13 - 6.21 = 8.92
    con.execute("UPDATE fact_fuel_txn SET ts_local = '2026-01-06 17:00:00' WHERE txn_id = 'FC0000002'")
    row = by_txn(find_missed(load_inputs(con, kit.test_cfg()), params, include_unverified=False))["FC0000002"]
    assert row["net_savings_usd"] == 8.92
    assert (row["price_gap_usd"], row["actual_detour_cost_usd"], row["alt_detour_cost_usd"]) == (12.00, 3.13, 6.21)
    assert row["is_peak"] is True and row["multiplier"] == 1.17


def test_an_on_route_alternative_beats_the_price_gap_when_the_station_used_was_a_detour(world):
    _, con, params, _ = world
    # B is on F1's route (0.0 mi / 0 min). Station A, where the driver stopped, was a 1.0 mi / 3 min detour.
    #   cost(A) = 322.816667 ; cost(B) = 80*3.800 = 304.000000 (no detour at all)
    #   net saving = 322.816667 - 304.000000 = 18.816667 -> 18.82
    #   price gap x gallons alone is (4.000 - 3.800) * 80 = 16.00, so the saving is above it by the detour avoided: 2.816667 (2.82)
    #   breakdown: 16.00 + 2.82 - 0.00 = 18.82
    con.execute("UPDATE ref_detour SET extra_miles = 0.0, extra_minutes = 0.0 WHERE route_id = 'TUC-R01' AND station_id = 'STN-002'")
    row = by_txn(find_missed(load_inputs(con, kit.test_cfg()), params, include_unverified=False))["FC0000001"]
    assert row["net_savings_usd"] == 18.82
    assert row["net_savings_usd"] > row["price_gap_usd"] == 16.00
    assert (row["price_gap_usd"], row["actual_detour_cost_usd"], row["alt_detour_cost_usd"]) == (16.00, 2.82, 0.00)


def test_the_detour_limit_judges_alternatives_not_the_station_used(world):
    _, con, params, _ = world
    # F1's own station A is now a 40 minute detour (off-peak), far over the 15 minute limit. It is still priced:
    #   cost(A) = 320.00 + (1.0/6)*4.000 = 0.666667 + (40/60)*38 = 25.333333 + 0.250000 = 346.250000
    #   cost(B) = 309.566667 (6 minutes, inside the limit) ; net saving = 346.25 - 309.566667 = 36.683333 -> 36.68
    con.execute("UPDATE ref_detour SET extra_minutes = 40 WHERE route_id = 'TUC-R01' AND station_id = 'STN-001'")
    row = by_txn(find_missed(load_inputs(con, kit.test_cfg()), params, include_unverified=False))["FC0000001"]
    assert row["net_savings_usd"] == 36.68 and row["actual_extra_minutes"] == 40.0
    # A 16 minute alternative is over the limit and is never counted: B at 16 minutes leaves F1 and F2 with no alternative.
    con.execute("UPDATE ref_detour SET extra_minutes = 16 WHERE route_id = 'TUC-R01' AND station_id = 'STN-002'")
    rows = by_txn(find_missed(load_inputs(con, kit.test_cfg()), params, include_unverified=False))
    assert "FC0000001" not in rows and "FC0000002" not in rows and "FC0000003" in rows


def test_the_parts_always_add_up_to_the_saving(world):
    _, _, params, inputs = world
    from fleetfuel.analytics.missed import actual_cost, best_alternative, breakdown
    # Unrounded: net = price gap x gallons + detour cost at the station used - detour cost to the alternative, for every missed fill.
    checked = 0
    for fill in inputs.fills:
        actual = actual_cost(fill, inputs, params)
        alt = best_alternative(fill, actual[0], inputs, params, include_unverified=True) if actual else None
        if alt is None:
            continue
        parts = breakdown(fill, actual[0], alt)
        assert alt["saving"] == pytest.approx(parts["price_gap_usd"] + parts["actual_detour_cost_usd"] - parts["alt_detour_cost_usd"], abs=1e-9)
        checked += 1
    assert checked == 3
    # Rounded: the displayed parts add to the displayed saving to within one cent, in both versions of the rows.
    for include in (False, True):
        for r in find_missed(inputs, params, include_unverified=include):
            assert r["net_savings_usd"] == pytest.approx(r["price_gap_usd"] + r["actual_detour_cost_usd"] - r["alt_detour_cost_usd"], abs=0.01)

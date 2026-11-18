"""Polish-round checks: no silent defaults, round once, plain-English reasons, scout counts, any feasible insertion.

Uses the mini hub from tests/fixtures/analytics/hubkit.py (driver $38.00 an hour, wear $0.25 a mile,
reserve 25 gal, fills to 95% of a 120 gal tank, 6.0 mpg, route day limit 840 minutes).
"""

import importlib.util
from pathlib import Path

import duckdb
import pytest

from fleetfuel.analytics import detour_options, partner_scout
from fleetfuel.analytics.missed import alternative_cost
from fleetfuel.analytics.net_savings import Detour, SavingsParams, best_feasible_insertion, load_inputs

OFF = 1.0  # the rush-hour multiplier of an off-peak stop
KIT_PATH = Path(__file__).parent / "fixtures" / "analytics" / "hubkit.py"
spec = importlib.util.spec_from_file_location("analytics_hubkit_polish", KIT_PATH)
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)


@pytest.fixture
def world(tmp_path):
    """cfg, open connection, params and loaded inputs for the mini hub."""
    cfg = kit.test_cfg()
    con = duckdb.connect(str(kit.build_mini_hub(tmp_path / "mini.duckdb")))
    params = SavingsParams.from_config(cfg)
    yield cfg, con, params, load_inputs(con, cfg)
    con.close()


def test_missing_base_minutes_raises_and_names_the_route(world):
    cfg, con, params, _ = world
    con.execute("UPDATE dim_route SET base_minutes = NULL WHERE route_id = 'TUC-R01'")
    inputs = load_inputs(con, cfg)
    fill = next(f for f in inputs.fills if f["route_id"] == "TUC-R01")
    with pytest.raises(ValueError, match="TUC-R01"):
        alternative_cost(fill, "STN-002", inputs, params)


def test_savings_are_rounded_once_from_unrounded_totals(world):
    _, _, params, inputs = world
    # G=72 (the typical fill), price 4.000, no miles, stop after stop 1 of TUC-R01 at 10:25 (off-peak). Driver time only: (t/60)*38.
    # Baseline detour 1.004 dollars (t = 1.004*60/38 min), option detour 0.006 dollars.
    #   totals: 289.004 and 288.006 ; saving 0.998 -> 1.00
    #   (rounding each total first gives 289.00 - 288.01 = 0.99, one cent off)
    confirmed = {"truck_access": "confirmed"}
    base = detour_options.build_option(inputs, params, "TUC-R01", "A", confirmed, 4.0, [Detour(1, 0.0, 1.004 * 60 / 38)], 6.0)
    opt = detour_options.build_option(inputs, params, "TUC-R01", "B", confirmed, 4.0, [Detour(1, 0.0, 0.006 * 60 / 38)], 6.0)
    assert (base["total_cost_usd"], opt["total_cost_usd"]) == (289.0, 288.01)
    assert detour_options.saving_vs_baseline(base, opt) == 1.00


def test_reason_text_is_plain_english_for_tiny_detours():
    tiny = {"net_savings_vs_baseline_usd": 12.34, "extra_miles": 0.04, "extra_minutes_effective": 0.4}
    text = detour_options.reason_text(tiny, 72, False)
    # v1.6: extra miles that round under 0.1 and under 1 extra minute read as no extra driving
    assert text == "Saves $12.34 on a 72-gallon fill with almost no extra driving."
    one = {"net_savings_vs_baseline_usd": 3.0, "extra_miles": 1.5, "extra_minutes_effective": 1.0}
    assert "1.5 extra miles and 1 minute of extra driving" in detour_options.reason_text(one, 72, False)
    assert " - " not in text and chr(0x2014) not in text
    # one minute or more, or 0.1 miles or more after rounding, keeps the numbers
    assert "almost no extra driving" not in detour_options.reason_text({**tiny, "extra_miles": 0.04, "extra_minutes_effective": 1.0}, 72, False)
    assert "almost no extra driving" not in detour_options.reason_text({**tiny, "extra_miles": 0.06, "extra_minutes_effective": 0.4}, 72, False)


def test_scout_counts_existing_fills_even_without_a_detour_entry(world):
    cfg, con, params, _ = world
    # X1 is the 100 gal fill already at STN-004 on TUC-R02. Remove that station's detour entry.
    # The discount applies at the pump, so at 10 cents it still saves 100 * 10 / 100 = 10.00.
    con.execute("DELETE FROM ref_detour WHERE station_id = 'STN-004'")
    scout = {e["station_id"]: e for e in partner_scout.compute_scout(load_inputs(con, cfg), params, cfg)}
    p10 = {p["discount_cpg"]: p for p in scout["STN-004"]["curve"]}[10]
    assert p10["fills_existing"] == 1 and p10["gallons_existing"] == 100.0
    assert p10["annual_net_savings_usd"] == 10.0  # no shifted fill: the candidate has no detour on any route


def test_any_feasible_insertion_is_used_not_just_the_cheapest(world):
    _, _, params, _ = world
    # 80 gal fill, 6.0 mpg, price 3.850, base route day 835 min (limit 840).
    #   Option 1 (cheapest): 0.5 mi / 8 min, but 835 + 8 = 843 > 840, so it does not fit the day.
    #   Option 2: 3.0 mi / 4 min, 835 + 4 = 839 <= 840 and 34 gal on board covers 0.5 gal: feasible.
    #   cost(2) = 80*3.850 = 308.000000 + (3.0/6)*3.850 = 1.925000 + (4/60)*38 = 2.533333 + 3.0*0.25 = 0.750000 = 313.208333
    options = [Detour(1, 0.5, 8.0), Detour(2, 3.0, 4.0)]
    found = best_feasible_insertion(options, 80, 3.85, 6.0, 120, 835, params, OFF)  # OFF: off-peak, so the hand arithmetic stays about feasibility
    assert found is not None and found[1] == options[1]
    assert found[0] == pytest.approx(313.208333, abs=1e-6)
    # With no option that fits, there is no alternative at all.
    assert best_feasible_insertion([options[0]], 80, 3.85, 6.0, 120, 835, params, OFF) is None

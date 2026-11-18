"""Why the lower-cost option wins (contract 13.1): `why` and `why_line` on every missed row."""

import importlib.util
import json
import tempfile
from pathlib import Path

import duckdb

from fleetfuel.analytics.missed import find_missed, why_line, why_reason
from fleetfuel.analytics.net_savings import Detour, SavingsParams, load_inputs

ROOT = Path(__file__).resolve().parents[1]
MISSED = ROOT / "site" / "data" / "missed.json"
KIT_PATH = Path(__file__).parent / "fixtures" / "analytics" / "hubkit.py"
spec = importlib.util.spec_from_file_location("analytics_hubkit_why", KIT_PATH)
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)


def line(actual_price, alt_price, actual_miles, alt_miles, actual_min=10.0, alt_min=10.0, actual_cost=2.0, alt_cost=1.0):
    """The (why, why_line) for two options with the given prices, detour miles, minutes and detour costs."""
    fill, alt = {"price_paid": actual_price}, {"alt_price": alt_price, "alt_detour": Detour(0, alt_miles, alt_min)}
    parts = {"actual_detour_cost_usd": actual_cost, "alt_detour_cost_usd": alt_cost}
    reason = why_reason(fill, alt, parts)
    return reason, why_line(reason, fill, alt, Detour(0, actual_miles, actual_min))


def test_shorter_detour_with_higher_price():
    assert line(5.0, 5.1, 9.0, 4.0) == ("shorter detour", "Higher pump price, but saves a 5-mile detour.")


def test_shorter_detour_with_same_price():
    assert line(5.0, 5.0, 9.0, 4.0) == ("shorter detour", "Same pump price, and saves a 5-mile detour.")


def test_lower_price_only_more_miles():
    got = line(5.0, 4.9, 1.0, 1.3, actual_cost=1.0, alt_cost=2.0)
    assert got == ("lower price", "Lower pump price, worth the extra 0.3-mile detour.")


def test_lower_price_only_same_miles_more_minutes():
    got = line(5.0, 4.9, 1.0, 1.0, 5.0, 8.0, 1.0, 2.0)
    assert got == ("lower price", "Lower pump price, worth the slightly longer detour.")


def test_both():
    assert line(5.0, 4.9, 9.2, 1.0) == ("both", "Lower pump price and saves an 8.2-mile detour.")


def test_under_a_tenth_of_a_mile_says_shorter_detour():
    assert line(5.0, 5.1, 1.04, 1.0, 10.0, 4.0)[1] == "Higher pump price, but a shorter detour."
    assert line(5.0, 4.9, 1.04, 1.0, 10.0, 4.0)[1] == "Lower pump price and a shorter detour."


def test_an_before_eight_and_eleven():
    assert "an 8.2-mile" in line(5.0, 4.9, 9.2, 1.0)[1]
    assert "an 11-mile" in line(5.0, 4.9, 12.0, 1.0)[1]
    assert "an 18-mile" in line(5.0, 4.9, 19.0, 1.0)[1]
    assert "a 7-mile" in line(5.0, 4.9, 8.0, 1.0)[1]


def test_whole_miles_drop_the_point_zero():
    assert "a 5-mile" in line(5.0, 5.1, 9.0, 4.0)[1]
    assert "5.0" not in line(5.0, 5.1, 9.0, 4.0)[1]
    assert "a 5.5-mile" in line(5.0, 5.1, 9.5, 4.0)[1]


def test_every_row_agrees_with_its_prices_and_costs():
    """On the mini hub, in both lists, `why` follows the prices and detour costs of the row."""
    cfg = kit.test_cfg()
    with tempfile.TemporaryDirectory() as tmp:
        con = duckdb.connect(str(kit.build_mini_hub(Path(tmp) / "mini.duckdb")))
        params, inputs = SavingsParams.from_config(cfg), load_inputs(con, cfg)
        for include in (False, True):
            rows = find_missed(inputs, params, include)
            assert rows
            for r in rows:
                lower = r["alt_price_per_gal"] < r["actual_price_per_gal"]
                assert r["why"] in {"lower price", "shorter detour", "both"}
                assert (r["why"] in {"lower price", "both"}) == lower
                assert r["why_line"].endswith(".")
        con.close()


def test_real_hand_check_rows_keep_their_figures():
    """The three hand-check rows in site/data/missed.json keep their figures and their why; new fields arrive together or not at all."""
    data = json.loads(MISSED.read_text())
    expected = {
        # All three show the alt detour balanced by one cent (own rounding 8.82 / 2.43 / 0.62 -> shown 8.83 / 2.42 / 0.63).
        "FC5659816": (9.7, 46.07, 8.83, 46.94, "both", "Lower pump price and saves a 17.8-mile detour."),
        "FC8960414": (9.27, 1.89, 2.42, 8.74, "lower price", "Lower pump price, worth the extra 0.1-mile detour."),
        "FC4426227": (-7.53, 14.88, 0.63, 6.72, "shorter detour", "Higher pump price, but saves a 6.4-mile detour."),
    }
    for key in ("rows", "rows_incl_unverified"):
        by_id = {r["txn_id"]: r for r in data[key]}
        for txn, parts in expected.items():
            r = by_id[txn]
            assert (r["price_gap_usd"], r["actual_detour_cost_usd"], r["alt_detour_cost_usd"], r["net_savings_usd"]) == parts[:4]
            assert (r["why"], r["why_line"]) == parts[4:]
        for r in data[key]:
            assert ("why" in r) == ("why_line" in r)


def test_miles_saved_matches_the_rounded_miles_the_panel_prints():
    """5.149 exports as 5.15 and the page prints 5.2; 0.24 prints 0.2. So the line says 5 miles, never 4.9."""
    from fleetfuel.analytics.missed import shown_miles_saved
    from fleetfuel.analytics.net_savings import Detour
    assert shown_miles_saved(Detour(0, 5.149, 9.0), {"alt_detour": Detour(0, 0.24, 1.0)}) == 5.0
    # 0.35 is stored just below 0.35, so JavaScript's toFixed(1) prints 0.3; 2.0 - 0.3 = 1.7.
    assert shown_miles_saved(Detour(0, 2.0, 9.0), {"alt_detour": Detour(0, 0.35, 1.0)}) == 1.7

"""Contract v1.7: rate headlines (cents per gallon), the Tucson partner share and derived headline assumptions.

Uses the mini hub (tests/fixtures/analytics/hubkit.py): 7 Tucson yard trucks, 560 card gallons, missed
savings $34.72 confirmed and $45.46 with unverified stations (hand-checked in test_analytics_handchecked).
"""

import copy
import importlib.util
import json
from pathlib import Path

import duckdb
import pytest

from fleetfuel import analytics, export
from fleetfuel.export import to_site

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("analytics_hubkit", HERE / "fixtures" / "analytics" / "hubkit.py")
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)
RAW_FIXTURE = HERE / "fixtures" / "pipeline"


def built(tmp_path, prepare=None):
    """Mini hub (optionally edited by `prepare(con)`), analytics run, export run. Returns (out dir, db path)."""
    cfg = kit.test_cfg()
    db = kit.build_mini_hub(tmp_path / "m.duckdb")
    if prepare:
        con = duckdb.connect(str(db))
        prepare(con)
        con.close()
    analytics.run(cfg, db_path=db)
    export.run(cfg, db_path=db, out_dir=tmp_path / "out", raw_dir=RAW_FIXTURE, generated_on="2026-11-17")
    return tmp_path / "out", db


def load(out, name):
    return json.loads((out / name).read_text(encoding="utf-8"))


def test_missed_cpg_per_card_gallon_all_three_rates(tmp_path):
    out, _ = built(tmp_path)
    kpis, missed = load(out, "overview.json")["kpis"], load(out, "missed.json")
    # card gallons: 80 + 80 + 60 + 110 + 50 + 100 + 80 = 560
    # confirmed:       34.72 / 560 * 100 = 6.2000 -> 6.20 cents per card gallon
    # incl unverified: 45.46 / 560 * 100 = 8.1179 -> 8.12
    assert missed["card_gallons"] == 560
    assert kpis["missed_savings_cpg_card"] == 6.20
    assert missed["cpg_per_card_gallon"] == 6.20
    assert missed["cpg_per_card_gallon_incl_unverified"] == 8.12


def test_scout_cpg_captured_hand_checked(tmp_path):
    out, _ = built(tmp_path)
    s = load(out, "partner_scout.json")
    curve = {p["discount_cpg"]: p for e in s["stations"] if e["station_id"] == "STN-004" for p in e["curve"]}
    # at 10 cents: gallons_per_month = (100 existing + 80 shifted) * 365 / 365 / 12 = 15.0
    # deal value = existing 100 * 0.10 = 10.00 + moved fill 8.00 (see test_deal_value_cases) = 18.00 a year
    # 18.00 / (15.0 * 12) * 100 = 10.00 cents per captured gallon, exactly the discount and never above it
    assert curve[10]["gallons_per_month"] == 15.0 and curve[10]["annual_net_savings_usd"] == 18.0
    assert curve[10]["net_savings_cpg_captured"] == 10.0
    # at 0 cents the deal creates nothing: 0 / (8.3 * 12) * 100 = 0
    assert curve[0]["net_savings_cpg_captured"] == 0.0
    assert all("net_savings_cpg_captured" in p for e in s["stations"] for p in e["curve"])


def test_cents_per_gallon_is_none_without_gallons():
    assert analytics.cents_per_gallon(12.0, 0.0) is None
    assert analytics.cents_per_gallon(34.72, 560.0) == 6.2


def test_tucson_partner_share_counts_only_tucson_yard_trucks(tmp_path):
    def prepare(con):
        con.execute("UPDATE fact_fuel_txn SET station_id = 'STN-002' WHERE txn_id = 'FC0000001'")  # TUC-01, 80 gal at partner B
        con.execute("INSERT INTO dim_truck VALUES ('PHX-01', 'PHX_DC', 'linehaul', 200, 6.0)")
        con.execute("INSERT INTO fact_fuel_txn VALUES ('FC0000099', 'PHX-01', 'STN-002', '2026-01-20 06:00:00', 200.0, 3.9, 780.0, "
                    "1, false, 99, 0.99, 'name+brand+gps')")  # a Phoenix truck at the partner must not count
    out, _ = built(tmp_path, prepare)
    k = load(out, "overview.json")["kpis"]
    # Tucson trucks: 80 partner gal / 560 gal = 0.143 (all-fleet card gallons would give 280 / 760 = 0.368)
    assert k["tucson_card_gallons_partner_share"] == 0.143
    assert "partner_share_gallons" not in k


def test_tucson_partner_share_is_zero_without_partner_fills(tmp_path):
    out, _ = built(tmp_path)
    assert load(out, "overview.json")["kpis"]["tucson_card_gallons_partner_share"] == 0.0


def test_derived_route_miles_is_the_mean_of_tucson_local_routes(tmp_path):
    db = kit.build_mini_hub(tmp_path / "m.duckdb")
    con = duckdb.connect(str(db))
    con.execute("UPDATE dim_route SET base_miles = 30.0 WHERE route_id = 'TUC-R01'")
    con.execute("UPDATE dim_route SET base_miles = 41.5 WHERE route_id = 'TUC-R02'")
    con.execute("UPDATE dim_route SET base_miles = 20.0 WHERE route_id = 'TUC-R03'")
    con.execute("INSERT INTO dim_route VALUES ('PHX-L01', 'PHX_DC', '[]', 500.0, 600.0, NULL)")  # linehaul is left out
    entry = to_site.derived_assumption("derived.tucson_local_route_miles_per_day", con, kit.test_cfg())
    con.close()
    # (30.0 + 41.5 + 20.0) / 3 = 30.5
    assert entry == {"key": "derived.tucson_local_route_miles_per_day", "value": 30.5, "source": "derived",
                     "note": "synthetic-route assumption: openrouteservice road miles over the synthetic route plans"}


def test_meta_marks_every_headline_entry_config_or_derived(tmp_path):
    out, _ = built(tmp_path)
    entries = load(out, "meta.json")["headline_assumptions"]
    assert {e["source"] for e in entries} == {"config", "derived"}
    derived = [e for e in entries if e["key"].startswith("derived.")]
    assert [e["value"] for e in derived] == [20.0]  # the mini hub's three routes are 20.0 miles each
    assert all(e["source"] == "config" for e in entries if not e["key"].startswith("derived."))


def test_unknown_derived_key_raises(tmp_path):
    cfg = copy.deepcopy(kit.test_cfg())
    cfg["site"]["headline_assumption_keys"] = ["derived.no_such_thing"]
    con = duckdb.connect(str(kit.build_mini_hub(tmp_path / "m.duckdb")), read_only=True)
    with pytest.raises(KeyError, match="derived.no_such_thing"):
        to_site.write_meta(tmp_path, cfg, "2026-11-17", 7, con)
    con.close()


def test_no_card_gallons_gives_null_rates_and_share(tmp_path):
    def prepare(con):
        con.execute("DELETE FROM fact_fuel_txn")
    out, _ = built(tmp_path, prepare)
    k, m = load(out, "overview.json")["kpis"], load(out, "missed.json")
    assert k["missed_savings_cpg_card"] is None and k["tucson_card_gallons_partner_share"] is None
    assert m["cpg_per_card_gallon"] is None and m["cpg_per_card_gallon_incl_unverified"] is None


def test_scout_method_says_cpg_can_exceed_the_discount(tmp_path):
    out, _ = built(tmp_path)
    assert "at most the discount on the gallons it captures" in load(out, "partner_scout.json")["method"]


def test_deal_value_cases():
    from fleetfuel.analytics.partner_scout import deal_value
    # Fill X2 of the mini hub (80 gal, route R02, 10 cent discount at S). Rates: mpg 6, $38/h, $0.25/mile.
    #   as made at A (posted 4.00, detour 1.0 mi, 3 min): 80*4.00 + (1/6)*4.00 + 3/60*38 + 1*0.25 = 320 + 0.6667 + 1.90 + 0.25 = 322.8167
    #   at S with X=10 (price 3.85, detour 2.0 mi, 4 min): 308 + (2/6)*3.85 + 4/60*38 + 2*0.25 = 308 + 1.2833 + 2.5333 + 0.50 = 312.3167
    #   gap_10 = 10.50; at X=0 (price 3.95): 316 + 1.3167 + 2.5333 + 0.50 = 320.35, gap_0 = 2.4667 (positive, under the $5 threshold)
    #   deal value = min(10.50 - 2.4667, 80 * 10 / 100 = 8.00) = min(8.0333, 8.00) = 8.00 (the discount cap binds)
    assert round(deal_value(10.50, 2.4667, 80.0, 10), 2) == 8.00
    # gap_0 negative (S was dearer at posted prices): nothing to subtract, so min(6.00 - 0, 8.00) = 6.00
    assert deal_value(6.00, -1.50, 80.0, 10) == 6.00
    # a fill that already moved at X=0 (gap_0 = 7.00 above the threshold, gap_10 = 14.00): min(14.00 - 7.00, 8.00) = 7.00
    assert deal_value(14.00, 7.00, 80.0, 10) == 7.00
    # an existing fill at S gets G * X / 100 = 100 * 10 / 100 = 10.00, checked through the scout in the next test


def test_cpg_captured_never_exceeds_the_discount_at_any_point(tmp_path):
    out, _ = built(tmp_path)
    s = load(out, "partner_scout.json")
    for e in s["stations"]:
        for p in e["curve"]:
            assert p["net_savings_cpg_captured"] <= p["discount_cpg"], (e["station_id"], p)

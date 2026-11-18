"""Pre-review fixes (contract v1.6): yard fills kept apart from card fills, per-truck figures, headline assumptions.

Uses the mini hub (tests/fixtures/analytics/hubkit.py). Yard rows go into the real fact_yard_fuel table that db.create_schema makes.
"""

import copy
import importlib.util
import json
from pathlib import Path

import duckdb
import pytest

from fleetfuel import analytics, config, export
from fleetfuel.analytics.missed import find_missed
from fleetfuel.analytics.net_savings import SavingsParams, load_inputs
from fleetfuel.export import to_site

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("analytics_hubkit", HERE / "fixtures" / "analytics" / "hubkit.py")
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)



def tank_cfg():
    """The mini hub config with the Phoenix DC tank switched on."""
    cfg = kit.test_cfg()
    cfg["yards"]["phoenix_dc"]["onsite_fuel"] = True
    return cfg


def add_yard(db_path, rows=((100.0, 3.50), (200.0, 3.60))):
    """Two Phoenix DC yard fills: 100 gal at 3.500 and 200 gal at 3.600 (350.00 + 720.00 = 1070.00 over 300 gal)."""
    con = duckdb.connect(str(db_path))
    for i, (gal, price) in enumerate(rows):
        con.execute("INSERT INTO fact_yard_fuel VALUES (?, 'PHX_DC', ?, ?, ?, ?)",
                    [f"PHX-0{i + 1}", f"2026-01-0{i + 5} 05:00:00", gal, price, round(gal * price, 2)])
    con.close()


@pytest.fixture(autouse=True)
def offline_routing(monkeypatch):
    """Tests never call openrouteservice."""
    from fleetfuel import routing

    def cannot_draw(*args, **kwargs):
        raise RuntimeError("routing is switched off in tests")

    monkeypatch.setattr(routing, "detour_leg_path", cannot_draw, raising=False)


def test_card_and_yard_gallons_are_kept_apart(tmp_path):
    cfg, db = tank_cfg(), kit.build_mini_hub(tmp_path / "m.duckdb")
    add_yard(db)
    analytics.run(cfg, db_path=db)
    con = duckdb.connect(str(db))
    results = analytics.read_results(con)
    k = results["kpis"]
    # card 560 gal (hand-checked in test_export_site); yard 100 + 200 = 300 gal; total 860
    assert (k["card_gallons"], k["yard_gallons"], k["total_gallons"]) == (560, 300, 860)
    # yard average = 1070.00 / 300 = 3.5667 -> 3.567; card average is unchanged by the yard fills: 2255.00 / 560
    assert k["avg_yard_price_per_gal"] == 3.567
    assert k["avg_net_price_per_gal"] == pytest.approx(2255.0 / 560, abs=0.0006)
    assert k["tucson_card_gallons_partner_share"] == 0.0
    # yard fills never become missed savings: same total and rows as without the yard table
    assert k["missed_savings_usd"] == 34.72
    assert {r["txn_id"] for r in results["missed"]["rows"]} == {"FC0000001", "FC0000002", "FC0000003"}
    con.close()


def test_partner_share_is_of_card_gallons_not_yard_gallons(tmp_path):
    cfg, db = tank_cfg(), kit.build_mini_hub(tmp_path / "m.duckdb")
    con = duckdb.connect(str(db))
    con.execute("UPDATE fact_fuel_txn SET station_id = 'STN-002' WHERE txn_id = 'FC0000001'")  # 80 gal at the partner
    con.close()
    add_yard(db)
    analytics.run(cfg, db_path=db)
    con = duckdb.connect(str(db))
    # 80 partner gallons over 560 card gallons = 0.143 (not 80 / 860 = 0.093)
    assert analytics.read_results(con)["kpis"]["tucson_card_gallons_partner_share"] == 0.143
    con.close()


def test_tank_yard_with_missing_or_empty_yard_log_is_an_error(tmp_path):
    cfg, db = tank_cfg(), kit.build_mini_hub(tmp_path / "m.duckdb")
    with pytest.raises(ValueError, match="fact_yard_fuel is empty"):       # table exists (create_schema) but has no rows
        analytics.run(cfg, db_path=db)
    con = duckdb.connect(str(db))
    con.execute("DROP TABLE fact_yard_fuel")
    con.close()
    with pytest.raises(ValueError, match="fact_yard_fuel is missing"):
        analytics.run(cfg, db_path=db)


def test_zero_yard_gallons_are_allowed_only_when_no_yard_has_a_tank(tmp_path):
    cfg, db = kit.test_cfg(), kit.build_mini_hub(tmp_path / "m.duckdb")    # tank off
    analytics.run(cfg, db_path=db)
    con = duckdb.connect(str(db))
    k = analytics.read_results(con)["kpis"]
    assert k["yard_gallons"] == 0 and k["total_gallons"] == k["card_gallons"] == 560
    assert "No yard has an on-site tank" in con.execute("SELECT notes FROM an_result WHERE name = 'kpis'").fetchone()[0]
    con.close()


def test_per_truck_per_year_arithmetic(tmp_path):
    cfg, db = kit.test_cfg(), kit.build_mini_hub(tmp_path / "m.duckdb")
    analytics.run(cfg, db_path=db)
    con = duckdb.connect(str(db))
    r = analytics.read_results(con)
    # 7 trucks, period 2025-11-01..2026-10-31 = 365 days: total * 365 / 365 / 7
    assert r["kpis"]["missed_usd_per_truck_per_year"] == 4.96             # 34.72 / 7 = 4.96
    assert r["missed_incl_unverified"]["usd_per_truck_per_year"] == 6.49  # 45.46 / 7 = 6.494
    # a 73 day period scales by 365/73 = 5: 34.72 * 5 / 7 = 24.8
    assert analytics.per_truck_per_year(34.72, 73, 7) == 24.80
    con.close()


def test_missed_fill_net_saving_is_unchanged_by_the_break_even_change(tmp_path):
    cfg = kit.test_cfg()
    con = duckdb.connect(str(kit.build_mini_hub(tmp_path / "m.duckdb")))
    rows = {r["txn_id"]: r for r in find_missed(load_inputs(con, cfg), SavingsParams.from_config(cfg), include_unverified=False)}
    # F3 as hand-checked in test_analytics_handchecked: 247.733333 - 235.504167 = 12.229167 -> 12.23
    assert rows["FC0000003"]["net_savings_usd"] == 12.23
    con.close()


def read_meta(out):
    return json.loads((out / "meta.json").read_text(encoding="utf-8"))


def write_meta(out, cfg, trucks):
    """write_meta against a mini hub (the derived headline entries read dim_route)."""
    db = kit.build_mini_hub(out / "meta_hub.duckdb")
    con = duckdb.connect(str(db), read_only=True)
    try:
        to_site.write_meta(out, cfg, "2026-11-17", trucks, con)
    finally:
        con.close()


def test_meta_headline_assumptions_follow_config_order(tmp_path):
    cfg = kit.test_cfg()
    write_meta(tmp_path, cfg, 20)
    meta = read_meta(tmp_path)
    keys = config.get(cfg, "site.headline_assumption_keys")
    assert [a["key"] for a in meta["headline_assumptions"]] == keys
    assert meta["trucks_in_fleet"] == 20
    by_key = {a["key"]: a for a in meta["assumptions"]}
    for a in meta["headline_assumptions"]:
        if a["source"] == "config":
            assert by_key[a["key"]] == {k: v for k, v in a.items() if k != "source"}
        else:
            assert a["key"].startswith("derived.")
    cfg2 = copy.deepcopy(cfg)
    cfg2["site"]["headline_assumption_keys"] = list(reversed(keys))
    write_meta(tmp_path, cfg2, 20)
    assert [a["key"] for a in read_meta(tmp_path)["headline_assumptions"]] == list(reversed(keys))


def test_unknown_headline_key_fails_clearly(tmp_path):
    cfg = copy.deepcopy(kit.test_cfg())
    cfg["site"]["headline_assumption_keys"] = ["savings.no_such_key"]
    with pytest.raises(KeyError, match="savings.no_such_key"):
        write_meta(tmp_path, cfg, 20)


def test_hub_lists_the_yard_log_as_a_fourth_source(tmp_path):
    cfg, db = tank_cfg(), kit.build_mini_hub(tmp_path / "m.duckdb")
    add_yard(db)
    raw = tmp_path / "raw"
    raw.mkdir()
    for f in (HERE / "fixtures" / "pipeline").glob("*.csv"):
        (raw / f.name).write_bytes(f.read_bytes())
    (raw / "yard_fuel.csv").write_text(
        "FillDate,FillTime,Yard,Unit,Gallons,PricePerGal,Total\n2026-01-05,05:00,PHX_DC,PHX-01,100,3.5,350\n", encoding="utf-8")
    analytics.run(cfg, db_path=db)
    export.run(cfg, db_path=db, out_dir=tmp_path / "out", raw_dir=raw, generated_on="2026-11-17")
    hub = json.loads((tmp_path / "out" / "hub.json").read_text(encoding="utf-8"))
    assert len(hub["sources"]) == 4
    assert hub["sources"][-1]["name"] == "Phoenix DC tank log"
    assert "Not a card feed" in hub["sources"][-1]["note"]


def test_hub_without_the_yard_log_fails_when_a_yard_has_a_tank(tmp_path):
    cfg, db = tank_cfg(), kit.build_mini_hub(tmp_path / "m.duckdb")
    add_yard(db)
    analytics.run(cfg, db_path=db)
    raw = tmp_path / "raw"
    raw.mkdir()
    for f in (HERE / "fixtures" / "pipeline").glob("*.csv"):
        if f.name != "yard_fuel.csv":
            (raw / f.name).write_bytes(f.read_bytes())
    with pytest.raises(FileNotFoundError, match="tank log must be a source"):
        export.run(cfg, db_path=db, out_dir=tmp_path / "out", raw_dir=raw, generated_on="2026-11-17")


def test_break_even_uses_one_median_route_lower_middle_when_even(tmp_path):
    from fleetfuel.analytics import detour_options
    cfg = kit.test_cfg()
    con = duckdb.connect(str(kit.build_mini_hub(tmp_path / "m.duckdb")))
    inputs, params = load_inputs(con, cfg), SavingsParams.from_config(cfg)
    # STN-001 on R01 (1.0 mi, 3 min), R02 (1.0, 3) and R03 (0.5, 2). Price basis 4.000. Detour costs:
    #   R03: (0.5/6)*4 + (2/60)*38 + 0.5*0.25 = 0.333333 + 1.266667 + 0.125 = 1.725
    #   R01, R02: 0.666667 + 1.9 + 0.25 = 2.816667   -> middle of three is R01 (ties by route id): gap 2.816667/72 = 3.9 cents
    #   (all three stops happen at 10:25, off-peak, so the 1.17 index does not apply)
    got = detour_options.break_even_table(inputs, params, cfg, ["TUC-R01", "TUC-R02", "TUC-R03"])
    assert got["STN-001"] == {"cpg": 3.9, "route_id": "TUC-R01", "is_peak": False}
    con.close()


def test_missing_onsite_fuel_key_raises_instead_of_meaning_no_tank():
    cfg = copy.deepcopy(kit.test_cfg())
    del cfg["yards"]["phoenix_dc"]["onsite_fuel"]
    with pytest.raises(KeyError, match="yards.phoenix_dc.onsite_fuel"):
        analytics.tank_yards(cfg)
    del cfg["yards"]
    with pytest.raises(KeyError, match="'yards'"):
        analytics.tank_yards(cfg)


def test_tank_yard_error_uses_the_config_label(tmp_path):
    cfg, db = tank_cfg(), kit.build_mini_hub(tmp_path / "m.duckdb")
    assert analytics.tank_yards(cfg) == ["Phoenix DC"]
    with pytest.raises(ValueError, match="Phoenix DC has an on-site fuel tank"):
        analytics.run(cfg, db_path=db)

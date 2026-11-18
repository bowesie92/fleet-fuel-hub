"""Export writes every site data file in the shape docs/contracts.md section 7 says.

Uses the tiny hand-built hub (tests/fixtures/analytics/hubkit.py), never data/hub.duckdb.
"""

import importlib.util
import json
import re
from pathlib import Path

import duckdb
import pytest

from fleetfuel import analytics, config, export

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("analytics_hubkit", HERE / "fixtures" / "analytics" / "hubkit.py")
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)

RAW_FIXTURE = HERE / "fixtures" / "pipeline"
EM_DASH = chr(0x2014)
FILES = ["meta.json", "overview.json", "stations.geojson", "price_history.json", "routes.geojson", "detours.json",
         "missed.json", "partner_scout.json", "hub.json"]


@pytest.fixture(autouse=True)
def offline_routing(monkeypatch):
    """Tests never call openrouteservice: by default routing cannot draw a path (tests that need one patch it again)."""
    from fleetfuel import routing

    def cannot_draw(*args, **kwargs):
        raise RuntimeError("routing is switched off in tests")

    monkeypatch.setattr(routing, "detour_leg_path", cannot_draw, raising=False)


@pytest.fixture
def built(tmp_path):
    """Run analytics and export on the mini hub; return (cfg, out_dir, db_path)."""
    cfg = kit.test_cfg()
    db_path = kit.build_mini_hub(tmp_path / "mini.duckdb")
    out = tmp_path / "site_data"
    analytics.run(cfg, db_path=db_path)
    export.run(cfg, db_path=db_path, out_dir=out, raw_dir=RAW_FIXTURE, generated_on="2026-11-17")
    return cfg, out, db_path


def load(out, name):
    return json.loads((out / name).read_text(encoding="utf-8"))


def test_every_contract_file_is_written_and_flagged_synthetic(built):
    _, out, _ = built
    for name in FILES:
        assert load(out, name)["synthetic"] is True, name


def test_no_em_dashes_and_no_raw_customer_ids(built):
    _, out, _ = built
    for name in FILES:
        text = (out / name).read_text(encoding="utf-8")
        assert EM_DASH not in text, name
        if name != "hub.json":  # the Data hub shows raw source samples on purpose
            assert not re.search(r"CUST-\d{4}", text), name
    assert "Customer 014" in (out / "routes.geojson").read_text(encoding="utf-8")


def test_meta_has_every_assumption(built):
    cfg, out, _ = built
    meta = load(out, "meta.json")
    assert meta["generated_on"] == "2026-11-17"
    assert meta["period_start"] == "2025-11-01" and meta["period_end"] == "2026-10-31"
    assert meta["assumptions"] == config.describe_assumptions()
    assert {"key", "value", "note"} <= set(meta["assumptions"][0])


def test_overview_kpis(built):
    _, out, _ = built
    k = load(out, "overview.json")["kpis"]
    assert set(k) == {"total_gallons", "card_gallons", "yard_gallons", "avg_net_price_per_gal", "avg_yard_price_per_gal",
                      "missed_savings_usd", "missed_usd_per_truck_per_year", "missed_savings_cpg_card",
                      "tucson_card_gallons_partner_share"}
    assert k["yard_gallons"] == 0 and k["avg_yard_price_per_gal"] == 0.0 and k["card_gallons"] == 560
    assert k["total_gallons"] == 560                    # 80+80+60+110+50+100+80
    assert k["missed_savings_usd"] == 34.72             # confirmed-only total (hand-checked)
    assert k["tucson_card_gallons_partner_share"] == 0.0            # no fill happened at the partner station B
    # 80*4.000 + 80*3.950 + 60*4.100 + 110*4.200 + 50*3.920 + 100*3.950 + 80*4.000 = 2253.00 over 560 gal
    spend = 80 * 4.0 + 80 * 3.95 + 60 * 4.1 + 110 * 4.2 + 50 * 3.92 + 100 * 3.95 + 80 * 4.0
    assert k["avg_net_price_per_gal"] == pytest.approx(spend / 560, abs=0.0006)


def test_stations_geojson(built):
    _, out, _ = built
    fc = load(out, "stations.geojson")
    assert fc["type"] == "FeatureCollection"
    props = {f["properties"]["station_id"]: f["properties"] for f in fc["features"]}
    assert set(props) == {"STN-001", "STN-002", "STN-003", "STN-004"}
    expected = {"station_id", "name", "display_name", "city", "brand", "station_class", "truck_access", "is_partner",
                "partner_discount_cpg", "fills", "gallons", "avg_price_paid", "avg_posted_price", "break_even_cpg",
                "break_even_route_id", "break_even_is_peak"}
    assert set(props["STN-001"]) == expected
    assert props["STN-003"]["truck_access"] == "unverified"
    assert props["STN-002"]["is_partner"] is True and props["STN-002"]["partner_discount_cpg"] == 10.0
    assert props["STN-002"]["fills"] == 0 and props["STN-002"]["avg_price_paid"] is None
    assert props["STN-001"]["fills"] == 6 and props["STN-001"]["gallons"] == 460.0   # F1-F5 and X2
    # Break-even for B: price basis 3.900 - 0.100 = 3.800; detour cost to B on each Tucson route:
    #   R01 (2.0 mi, 6 min): (2/6)*3.8 + (6/60)*38 + 2*0.25 = 1.266667 + 3.8 + 0.5 = 5.566667
    #   R03 (1.5 mi, 5 min): (1.5/6)*3.8 + (5/60)*38 + 1.5*0.25 = 0.95 + 3.166667 + 0.375 = 4.491667
    # Two routes, so the lower-middle one (R03) is the median route; its own miles and minutes are used.
    # gap = 4.491667 / 72 (the typical fill) = 0.062384 -> 6.2 cents, at 10:25 (off-peak, no rush-hour factor, no schedule risk).
    # (Separate medians of miles and minutes would have given a different figure.)
    assert props["STN-002"]["break_even_cpg"] == 6.2
    assert props["STN-002"]["break_even_route_id"] == "TUC-R03" and props["STN-002"]["break_even_is_peak"] is False
    assert props["STN-004"]["break_even_cpg"] is not None
    coords = fc["features"][0]["geometry"]["coordinates"]
    assert coords == [-110.9, 32.2]  # [lon, lat]


def test_price_history_shape(built):
    _, out, _ = built
    ph = load(out, "price_history.json")
    assert {"week_start", "price_per_gal"} == set(ph["eia"][0])
    week = next(w for w in ph["stations"]["STN-001"] if w["week_start"] == "2026-01-05")
    assert set(week) == {"week_start", "avg_price_paid", "avg_posted_price", "gallons"}
    assert week["gallons"] == 320.0 and week["avg_price_paid"] == 4.044    # 4.04375 rounded to 3 places
    empty = next(w for w in ph["stations"]["STN-002"] if w["week_start"] == "2026-01-05")
    assert empty["avg_price_paid"] is None


def test_routes_geojson_labels(built):
    _, out, _ = built
    feats = load(out, "routes.geojson")["features"]
    routes = [f["properties"] for f in feats if f["properties"]["kind"] == "route"]
    assert [r["label"] for r in routes] == ["Route R01", "Route R02", "Route R03"]
    assert set(routes[0]) == {"kind", "route_id", "label", "yard", "base_miles", "base_minutes", "stop_count"}
    stops = [f["properties"] for f in feats if f["properties"]["kind"] == "stop"]
    assert {s["label"] for s in stops} == {"Tucson yard", "Customer 014"}


def test_missed_json_has_both_toggle_versions(built):
    _, out, _ = built
    m = load(out, "missed.json")
    assert m["total_usd"] == 34.72 and m["fill_count"] == 3
    assert m["trucks_in_fleet"] == 7 and m["usd_per_truck_per_year"] == 4.96                     # 34.72 * 365/365 / 7
    assert m["usd_per_truck_per_year_incl_unverified"] == 6.49                                  # 45.46 / 7 = 6.494
    assert m["total_usd_incl_unverified"] == 45.46 and m["fill_count_incl_unverified"] == 3
    assert [r["net_savings_usd"] for r in m["rows"]] == [13.25, 12.23, 9.24]           # largest first
    assert [r["net_savings_usd"] for r in m["rows_incl_unverified"]] == [22.97, 13.25, 9.24]
    row = m["rows"][0]
    assert set(row) == {"txn_id", "date", "truck_id", "route_id", "actual_station_id", "alt_station_id", "gallons",
                        "actual_price_per_gal", "alt_price_per_gal", "actual_extra_miles", "actual_extra_minutes",
                        "alt_extra_miles", "alt_extra_minutes", "actual_insert_after_stop", "alt_insert_after_stop",
                        "is_peak", "multiplier", "price_gap_usd", "actual_detour_cost_usd", "alt_detour_cost_usd",
                        "net_savings_usd", "alt_truck_access", "why", "why_line"}
    assert round(row["price_gap_usd"] + row["actual_detour_cost_usd"] - row["alt_detour_cost_usd"], 2) == row["net_savings_usd"]
    assert set(m["weekly"][0]) == {"week_start", "missed_savings_usd", "fills"}
    weeks = {w["week_start"]: w for w in m["weekly"]}
    assert weeks["2026-01-05"]["missed_savings_usd"] == 22.49 and weeks["2026-01-05"]["fills"] == 2   # 13.25 + 9.24
    assert weeks["2026-01-12"]["missed_savings_usd"] == 12.23
    assert round(sum(w["missed_savings_usd"] for w in m["weekly"]), 2) == m["total_usd"]
    incl_weeks = {w["week_start"]: w for w in m["weekly_incl_unverified"]}
    assert incl_weeks["2026-01-12"]["missed_savings_usd"] == 22.97


def test_detours_json_toggle_fields(built):
    _, out, _ = built
    d = load(out, "detours.json")
    assert d["typical_fill_gal"] == 72 and d["include_unverified_default"] is False
    assert d["price_basis"] == "average posted net price, last 4 weeks of the period"
    r = d["routes"]["TUC-R01"]
    assert {"baseline_station_id", "best_station_id", "best_reason", "best_station_id_incl_unverified",
            "best_reason_incl_unverified", "options"} <= set(r)
    option_keys = {"station_id", "truck_access", "insert_after_stop", "stop_time_local", "is_peak", "multiplier",
                   "extra_miles", "extra_minutes", "extra_minutes_effective", "price_per_gal",
                   "fuel_cost_usd", "detour_cost_usd", "total_cost_usd", "net_savings_vs_baseline_usd",
                   "before_last_delivery", "ranking_penalty_usd", "over_max_detour"}
    assert all(set(o) == option_keys for o in r["options"])
    # Route R01, 72 gal, price basis = last 4 weeks of the period (A 4.000, B 3.900-0.100 = 3.800, C 3.950).
    # Every option is inserted after stop 1 (planned 10:00 + 25 minutes unloading = 10:25, off-peak, after the last customer).
    #   A (baseline, most used on R01): fuel 288.00; detour (1/6)*4.000 + 1.9 + 0.25 = 2.816667 -> 2.82; total 290.82
    #   B: fuel 273.60; detour (2/6)*3.800 + 3.8 + 0.5 = 5.566667 -> 5.57; total 279.17; saves 290.816667 - 279.166667 = 11.65
    #   C (unverified): fuel 284.40; detour 0.658333 + 1.9 + 0.25 = 2.808333 -> 2.81; total 287.21; saves 3.608333 -> 3.61
    opts = {o["station_id"]: o for o in r["options"]}
    assert r["baseline_station_id"] == "STN-001"
    assert (opts["STN-002"]["fuel_cost_usd"], opts["STN-002"]["detour_cost_usd"], opts["STN-002"]["total_cost_usd"]) == (273.60, 5.57, 279.17)
    assert opts["STN-002"]["net_savings_vs_baseline_usd"] == 11.65 and opts["STN-001"]["net_savings_vs_baseline_usd"] == 0.0
    assert opts["STN-003"]["net_savings_vs_baseline_usd"] == 3.61 and opts["STN-003"]["truck_access"] == "unverified"
    assert (opts["STN-002"]["stop_time_local"], opts["STN-002"]["is_peak"], opts["STN-002"]["multiplier"]) == ("10:25", False, 1.0)
    assert opts["STN-002"]["extra_minutes_effective"] == 6.0 and opts["STN-002"]["ranking_penalty_usd"] == 0.0
    assert r["best_station_id"] == "STN-002" and r["best_station_id_incl_unverified"] == "STN-002"
    assert r["best_reason"] == "Saves $11.65 on a 72-gallon fill even after the 2.0 extra miles and 6 minutes of extra driving."
    # Top level (contract 11.3). Best stops: R01 saves 11.65 at B, R03 saves 11.63 (B: 278.091667 vs A: 289.725), R02 saves 2.07 (S).
    assert d["default_route_id"] == "TUC-R01"
    assert d["ranking_note"] == "Stops before the last delivery are ranked lower to protect the delivery schedule."
    assert d["rush_hour_note"] == ("Rush hour, weekdays 6 to 9 am and 4 to 7 pm: detour minutes count 1.17 times "
                                   "(Tucson Travel Time Index, Texas A&M 2025 Urban Mobility Report).")
    assert (d["price_period_start"], d["price_period_end"]) == ("2026-10-04", "2026-10-31")
    w = d["worked_example"]
    # Median basis price 3.950 (A 4.00, B 3.80, C 3.95, S 3.95). 12 min and 4 mi at 7 am (rush hour, 1.17): 14.04 effective minutes.
    #   driver (14.04/60)*38 = 8.892 ; fuel (4/6)*3.95 = 2.633333 ; wear 4*0.25 = 1.00 ; total 12.525333 -> 12.53
    #   gap 12.525333 / 72 * 100 = 17.396 -> 17.4 cents
    assert "schedule_risk_usd" not in w and (w["price_period_start"], w["price_period_end"]) == ("2026-10-04", "2026-10-31")
    assert w["total_usd"] == 12.53 and w["cpg_needed"] == 17.4 and w["fill_gal"] == 72
    assert w["sentence"] == ("For a 72-gallon fill at $3.950 a gallon (median Tucson posted price, Oct 4 to Oct 31, 2026), a 12-minute detour "
                             "at 7 am costs $12.53 ($8.89 driver time, $2.63 fuel, $1.00 wear), "
                             "so the station needs to be at least 17.4 cents a gallon cheaper to pay off.")


def test_partner_scout_json(built):
    cfg, out, _ = built
    s = load(out, "partner_scout.json")
    assert s["scenario_label"] == "Synthetic scenario"
    assert s["scope"] == "Tucson box, truck-confirmed stations that are not partners"
    assert s["default_discount_cpg"] == cfg["partner_scout"]["default_discount_cpg"]
    assert s["best_station_id_at_default"] == s["stations"][0]["station_id"]
    assert s["discount_steps_cpg"][0] == 0 and s["discount_steps_cpg"][-1] == 30
    st = next(x for x in s["stations"] if x["station_id"] == "STN-004")
    assert set(st) == {"station_id", "display_name", "lat", "lon", "annual_savings_without_deal_usd", "curve"}
    assert st["annual_savings_without_deal_usd"] == 0.0
    assert st["curve"][0]["annual_net_savings_usd"] == 0.0
    assert st["display_name"] == "Delta Fuel"
    assert [p["discount_cpg"] for p in st["curve"]] == s["discount_steps_cpg"]
    assert set(st["curve"][0]) == {"discount_cpg", "fills_existing", "gallons_existing", "fills_shifted",
                                   "gallons_shifted", "gallons_per_month", "annual_net_savings_usd",
                                   "annual_net_savings_per_truck_usd", "net_savings_cpg_captured"}
    assert s["trucks_in_fleet"] == 7
    assert {p["discount_cpg"]: p["annual_net_savings_per_truck_usd"] for p in st["curve"]}[10] == 2.57   # 18.00 / 7 = 2.5714
    assert {p["discount_cpg"]: p["annual_net_savings_usd"] for p in st["curve"]}[10] == 18.0   # hand-checked (10.00 + 8.00)


def test_hub_json_has_three_sources_without_a_yard_log(built, tmp_path):
    cfg, _, db_path = built
    raw = tmp_path / "raw_no_yard"
    raw.mkdir()
    for f in RAW_FIXTURE.glob("*.csv"):
        if f.name != "yard_fuel.csv":
            (raw / f.name).write_bytes(f.read_bytes())
    export.run(cfg, db_path=db_path, out_dir=tmp_path / "o2", raw_dir=raw, generated_on="2026-11-17")
    assert [s["file"] for s in load(tmp_path / "o2", "hub.json")["sources"]] == ["fuel_card.csv", "telematics.csv", "route_plans.csv"]


def test_hub_json(built):
    _, out, _ = built
    h = load(out, "hub.json")
    # the pipeline fixture folder has yard_fuel.csv, so the Phoenix DC tank log is the fourth source
    assert [s["file"] for s in h["sources"]] == ["fuel_card.csv", "telematics.csv", "route_plans.csv", "yard_fuel.csv"]
    assert all(len(s["sample"]) <= 5 and s["rows"] > 0 for s in h["sources"])
    assert h["clean_table"]["name"] == "fact_fuel_txn" and h["clean_table"]["rows"] == 7
    assert h["dq"][0]["check_name"] == "dedupe_swipes"
    assert h["accuracy"][0] == {"metric": "station_match_precision", "value": 1.0, "notes": "7 of 7 matched swipes went to the correct station."}


def test_stale_missed_paths_file_is_deleted_and_failed_rows_are_skipped(built, monkeypatch):
    cfg, out, db_path = built
    from fleetfuel import routing

    def cannot_draw(*args, **kwargs):
        raise RuntimeError("no cached road path")

    monkeypatch.setattr(routing, "detour_leg_path", cannot_draw, raising=False)
    (out / "missed_paths.geojson").write_text("{}", encoding="utf-8")   # a stale placeholder from the old contract
    export.run(cfg, db_path=db_path, out_dir=out, raw_dir=RAW_FIXTURE)
    assert not (out / "missed_paths.geojson").exists()
    assert load(out, "detour_paths.geojson")["features"] == []            # every path failed: nothing drawn, the site draws straight lines


def test_detour_paths_use_routing_detour_leg_path_for_at_most_the_configured_rows(built, monkeypatch):
    cfg, out, db_path = built
    from fleetfuel import routing
    calls = []

    def fake_leg_path(cfg_, route_id, station_id, insert_after_stop):
        calls.append((route_id, station_id, insert_after_stop))
        if (route_id, station_id) == ("TUC-R01", "STN-002"):
            raise KeyError("no such leg")                                # a row that raises is skipped, the rest still draw
        return {"type": "LineString", "coordinates": [[-110.9, 32.1], [-110.8123456, 32.2], [-110.7, 32.3]]}

    monkeypatch.setattr(routing, "detour_leg_path", fake_leg_path, raising=False)
    limited = json.loads(json.dumps(cfg))
    limited["export"]["max_detour_paths"] = 4
    export.run(limited, db_path=db_path, out_dir=out, raw_dir=RAW_FIXTURE)
    # ref_detour rows in order: R01 A, R01 B, R01 C, R02 A (4 rows); R01-B raises, so 3 lines are written
    assert calls == [("TUC-R01", "STN-001", 1), ("TUC-R01", "STN-002", 1), ("TUC-R01", "STN-003", 1), ("TUC-R02", "STN-001", 1)]
    paths = load(out, "detour_paths.geojson")
    assert paths["synthetic"] is True and len(paths["features"]) == 3
    assert [f["properties"] for f in paths["features"]][0] == {"route_id": "TUC-R01", "station_id": "STN-001", "insert_after_stop": 1}
    assert all(f["geometry"]["type"] == "LineString" for f in paths["features"])
    assert paths["features"][0]["geometry"]["coordinates"][1] == [-110.81235, 32.2]      # 5 decimals
    assert not (out / "missed_paths.geojson").exists()


def test_detour_paths_cover_every_ref_detour_row_by_default(built, monkeypatch):
    cfg, out, db_path = built
    from fleetfuel import routing
    monkeypatch.setattr(routing, "detour_leg_path", lambda *a, **k: {"type": "LineString", "coordinates": [[-110.9, 32.1], [-110.8, 32.2]]}, raising=False)
    export.run(cfg, db_path=db_path, out_dir=out, raw_dir=RAW_FIXTURE)
    assert len(load(out, "detour_paths.geojson")["features"]) == 8       # 3 + 2 + 3 ref_detour rows in the mini hub


def test_an_result_table_matches_contract_6_1(built):
    _, _, db_path = built
    con = duckdb.connect(str(db_path), read_only=True)
    cols = con.execute("SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name = 'an_result' ORDER BY ordinal_position").fetchall()
    assert cols == [("name", "NO"), ("payload", "NO"), ("notes", "YES")]
    notes = dict(con.execute("SELECT name, notes FROM an_result").fetchall())
    assert {"missed", "partner_scout", "detours", "price_history"} <= set(notes)
    assert "skipped" in notes["missed"] and EM_DASH not in notes["missed"]
    con.close()


def test_export_needs_analytics_first(tmp_path):
    cfg = kit.test_cfg()
    db_path = kit.build_mini_hub(tmp_path / "mini.duckdb")
    with pytest.raises(RuntimeError, match="analytics"):
        export.run(cfg, db_path=db_path, out_dir=tmp_path / "out", raw_dir=RAW_FIXTURE)


def test_empty_ref_detour_still_builds(tmp_path):
    """Before routing merges the hub has no detours: nothing can be priced, but nothing crashes."""
    cfg = kit.test_cfg()
    db_path = kit.build_mini_hub(tmp_path / "mini.duckdb", with_detours=False)
    analytics.run(cfg, db_path=db_path)
    export.run(cfg, db_path=db_path, out_dir=tmp_path / "out", raw_dir=RAW_FIXTURE)
    out = tmp_path / "out"
    assert load(out, "missed.json")["total_usd"] == 0.0
    assert load(out, "detours.json")["routes"] == {}
    assert all(p["annual_net_savings_usd"] >= 0 for e in load(out, "partner_scout.json")["stations"] for p in e["curve"])
    assert load(out, "overview.json")["kpis"]["total_gallons"] == 560


def test_detour_sample_fixture_has_the_ref_detour_columns():
    head = (HERE / "fixtures" / "analytics" / "ref_detour_sample.csv").read_text(encoding="utf-8").splitlines()[0]
    assert head == "route_id,station_id,insert_after_stop,extra_miles,extra_minutes"

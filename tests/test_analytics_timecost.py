"""Time costs (contract v1.11, SPEC section 6): rush-hour multiplier, reliability buffer, the detour limit, schedule risk as a
ranking penalty only, one cost per Test route option at its stop time, and the hours-of-service check (no overtime term).

Every number is worked out on paper in the comments. Pinned inputs (tests/fixtures/analytics/hubkit.py): driver $38/h,
wear $0.25/mile, 6 mpg, TTI 1.17, PTI 1.38, peak 06:00-09:00 and 16:00-19:00 on weekdays, ranking penalty $0.50 a minute,
longest detour 15 effective minutes, typical fill 72 gallons, 25 minutes unloading at a customer. In the mini hub every
route is: yard planned 09:30, customer planned 10:00, yard planned 11:00, so a stop after stop 1 happens at 10:25 (off-peak).

The detour used throughout: 2.0 miles and 4 minutes at a price of 3.85 a gallon.
    fuel burned  (2.0/6) * 3.85 = 1.283333      wear 2.0 * 0.25 = 0.50
"""

import copy
import importlib.util
import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from fleetfuel import analytics
from fleetfuel.analytics import detour_options
from fleetfuel.analytics.missed import find_missed
from fleetfuel.analytics.net_savings import (
    Detour, SavingsParams, detour_cost, is_feasible, load_inputs, multiplier, ranking_penalty_usd, stop_cost,
)

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("analytics_hubkit", HERE / "fixtures" / "analytics" / "hubkit.py")
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)

MONDAY_5PM = pd.Timestamp("2026-01-05 17:00")
SATURDAY_5PM = pd.Timestamp("2026-01-10 17:00")
ROUTES = ["TUC-R01", "TUC-R02", "TUC-R03"]


@pytest.fixture
def params():
    return SavingsParams.from_config(kit.test_cfg())


@pytest.fixture
def hub(tmp_path):
    """(db path, cfg) for a fresh mini hub that tests may edit before analysing."""
    return kit.build_mini_hub(tmp_path / "m.duckdb"), kit.test_cfg()


def edit(db, *statements):
    con = duckdb.connect(str(db))
    for sql in statements:
        con.execute(sql)
    con.close()


def set_arrivals(db, route_id, arrivals):
    """Change the planned_arrival of some stops of a route, e.g. {1: '06:35'} (stop number -> HH:MM)."""
    con = duckdb.connect(str(db))
    stops = json.loads(con.execute("SELECT stops_json FROM dim_route WHERE route_id = ?", [route_id]).fetchone()[0])
    for stop in stops:
        if stop["seq"] in arrivals:
            if arrivals[stop["seq"]] is None:
                del stop["planned_arrival"]
            else:
                stop["planned_arrival"] = arrivals[stop["seq"]]
    con.execute("UPDATE dim_route SET stops_json = ? WHERE route_id = ?", [json.dumps(stops), route_id])
    con.close()


def missed(db, cfg):
    con = duckdb.connect(str(db))
    rows = {r["txn_id"]: r for r in find_missed(load_inputs(con, cfg), SavingsParams.from_config(cfg), include_unverified=False)}
    con.close()
    return rows


def built_results(db, cfg):
    analytics.run(cfg, db_path=db)
    con = duckdb.connect(str(db))
    out = analytics.read_results(con)
    con.close()
    return out


def options_of(db, cfg, route="TUC-R01"):
    return {o["station_id"]: o for o in built_results(db, cfg)["detours"]["routes"][route]["options"]}


# ------------------------------------------------------------------ peak, off-peak, Saturday, buffer

def test_multiplier_peak_offpeak_and_saturday(params):
    assert multiplier(MONDAY_5PM, params) == 1.17                          # weekday, inside 16:00-19:00: TTI
    assert multiplier(pd.Timestamp("2026-01-05 10:30"), params) == 1.0     # weekday, between windows
    assert multiplier(SATURDAY_5PM, params) == 1.0                         # same clock time on a Saturday: off-peak
    assert multiplier(pd.Timestamp("2026-01-05 06:00"), params) == 1.17    # window start is inside
    assert multiplier(pd.Timestamp("2026-01-05 09:00"), params) == 1.0     # window end is outside


def test_detour_cost_off_peak_peak_and_saturday(params):
    off = detour_cost(3.85, 2.0, 4.0, 6.0, params, multiplier(pd.Timestamp("2026-01-05 10:30"), params))
    peak = detour_cost(3.85, 2.0, 4.0, 6.0, params, multiplier(MONDAY_5PM, params))
    sat = detour_cost(3.85, 2.0, 4.0, 6.0, params, multiplier(SATURDAY_5PM, params))
    # off-peak: 1.283333 + (4/60)*38 = 2.533333 + 0.50 = 4.316667
    assert off == pytest.approx(4.316667, abs=1e-6)
    # Monday 5 pm: minutes_eff = 4 * 1.17 = 4.68; time = (4.68/60)*38 = 2.964; total 1.283333 + 2.964 + 0.50 = 4.747333
    assert peak == pytest.approx(4.747333, abs=1e-6)
    assert sat == pytest.approx(off)                                       # Saturday 5 pm is off-peak


def test_reliability_buffer_uses_the_planning_time_index():
    cfg = kit.test_cfg()
    cfg["time_cost"]["use_reliability_buffer"] = True
    params = SavingsParams.from_config(cfg)
    assert multiplier(MONDAY_5PM, params) == 1.38
    # minutes_eff = 4 * 1.38 = 5.52; time = (5.52/60)*38 = 3.496; total 1.283333 + 3.496 + 0.50 = 5.279333
    assert detour_cost(3.85, 2.0, 4.0, 6.0, params, 1.38) == pytest.approx(5.279333, abs=1e-6)
    assert multiplier(pd.Timestamp("2026-01-05 10:30"), params) == 1.0     # off-peak is untouched by the switch


# ------------------------------------------------------------------ schedule risk is a ranking penalty only (and no overtime)

def test_the_detour_cost_has_no_schedule_risk_term(params):
    # 4 minutes at 3.85: 1.283333 + 2.533333 + 0.50 = 4.316667 wherever the stop sits on the route
    assert detour_cost(3.85, 2.0, 4.0, 6.0, params, 1.0) == pytest.approx(4.316667, abs=1e-6)
    assert not hasattr(params, "overtime_multiplier") and not hasattr(params, "shift_length_minutes")


def test_ranking_penalty_only_before_the_last_customer(params):
    # last customer is stop 1; a stop after stop 0 makes that customer wait: 4 effective minutes * $0.50 = 2.00
    assert ranking_penalty_usd(4.0, 0, 1, params) == pytest.approx(2.0)
    assert ranking_penalty_usd(4.0, 1, 1, params) == 0.0                   # after the last customer: nothing left to be late for
    assert ranking_penalty_usd(4.68, 0, 1, params) == pytest.approx(2.34)  # rush hour: 4 * 1.17 = 4.68 effective minutes * 0.50
    assert ranking_penalty_usd(4.0, 0, None, params) == 0.0                # a route with no customer has no delivery to protect


def test_risk_never_changes_a_missed_saving(hub):
    db, cfg = hub
    edit(db, "UPDATE ref_detour SET insert_after_stop = 0 WHERE route_id = 'TUC-R03' AND station_id = 'STN-002'")
    # B now sits before R03's last customer, at 09:30 (off-peak). Under v1.9 that cost 5 * 0.50 = 2.50 extra; now nothing:
    # F3 stays 12.229167 -> 12.23.
    assert missed(db, cfg)["FC0000003"]["net_savings_usd"] == 12.23


def test_a_long_route_day_does_not_change_missed_savings(hub):
    db, cfg = hub
    edit(db, "UPDATE dim_route SET base_minutes = 598 WHERE route_id = 'TUC-R03'")
    assert missed(db, cfg)["FC0000003"]["net_savings_usd"] == 12.23     # unchanged: no overtime term


def test_the_risk_rate_changes_the_ranking_but_no_dollar_figure(hub):
    db, cfg = hub
    # Make C confirmed so route R01 has two allowed alternatives, and put B before the last delivery (after stop 0, 09:30).
    edit(db, "UPDATE dim_station SET truck_access = 'confirmed' WHERE station_id = 'STN-003'",
         "UPDATE ref_detour SET insert_after_stop = 0 WHERE route_id = 'TUC-R01' AND station_id = 'STN-002'")
    calm, harsh = copy.deepcopy(cfg), copy.deepcopy(cfg)
    harsh["time_cost"]["schedule_risk_usd_per_minute"] = 3.0
    con = duckdb.connect(str(db))

    def results(c):
        return analytics.compute_results(con, c)

    lo, hi = results(calm), results(harsh)
    con.close()
    # Savings over the baseline A: B 11.65 (unchanged by risk), C 3.61. Ranking score = saving - penalty difference:
    #   rate 0.50: B penalty 6 * 0.50 = 3.00 -> 8.65 ; C no penalty -> 3.61 ; B is best
    #   rate 3.00: B penalty 6 * 3.00 = 18.00 -> -6.35 ; C 3.61 ; C is best (and the reason quotes its dollar saving 3.61, not a score)
    r_lo, r_hi = lo["detours"]["routes"]["TUC-R01"], hi["detours"]["routes"]["TUC-R01"]
    assert r_lo["best_station_id"] == "STN-002" and r_hi["best_station_id"] == "STN-003"
    assert r_hi["best_reason"].startswith("Saves $3.61 on a 72-gallon fill")
    penalties = {o["station_id"]: o["ranking_penalty_usd"] for o in r_hi["options"]}
    assert penalties["STN-002"] == 18.0 and penalties["STN-003"] == 0.0
    # Every dollar figure is identical at either rate.
    strip = lambda rows: [{k: v for k, v in o.items() if k != "ranking_penalty_usd"} for o in rows]   # noqa: E731
    assert strip(r_lo["options"]) == strip(r_hi["options"])
    for name in ("missed", "missed_incl_unverified", "partner_scout", "station_stats", "kpis"):
        assert lo[name] == hi[name], name
    assert lo["detours"]["worked_example"] == hi["detours"]["worked_example"]


# ------------------------------------------------------------------ max detour (effective minutes, alternatives only)

def test_max_detour_uses_effective_minutes(params):
    plenty = dict(tank_gal=120.0, gallons=80.0, mpg=6.0, route_base_minutes=300.0, params=params)
    assert is_feasible(detour=Detour(1, 2.0, 15.0), mult=1.0, **plenty)       # 15 <= 15
    assert not is_feasible(detour=Detour(1, 2.0, 16.0), mult=1.0, **plenty)   # 16 > 15
    # 13 minutes is fine off-peak but 13 * 1.17 = 15.21 is over the limit in rush hour
    assert is_feasible(detour=Detour(1, 2.0, 13.0), mult=1.0, **plenty)
    assert not is_feasible(detour=Detour(1, 2.0, 13.0), mult=1.17, **plenty)


def test_over_max_alternative_is_not_a_missed_saving(hub):
    db, cfg = hub
    assert {"FC0000001", "FC0000002", "FC0000003"} == set(missed(db, cfg))
    edit(db, "UPDATE ref_detour SET extra_minutes = 16 WHERE route_id = 'TUC-R01' AND station_id = 'STN-002'")
    # B is now 16 minutes away from route R01: F1 and F2 (both on R01) lose their alternative; F3 (R03) keeps it
    assert set(missed(db, cfg)) == {"FC0000003"}


# ------------------------------------------------------------------ the fill's own time, through find_missed

def test_a_peak_fill_costs_more_detour_time(hub):
    db, cfg = hub
    assert missed(db, cfg)["FC0000003"]["net_savings_usd"] == 12.23        # off-peak, as hand-checked before
    edit(db, "UPDATE fact_fuel_txn SET ts_local = '2026-01-12 17:00:00' WHERE txn_id = 'FC0000003'")   # a Monday, 5 pm
    # F3: actual A detour (0.5 mi, 2 min), alternative B detour (1.5 mi, 5 min), both multiplied by 1.17.
    #   A: (2*1.17/60)*38 = 1.482 + (0.5/6)*4.10 = 0.341667 + 0.125 = 1.948667
    #   B: (5*1.17/60)*38 = 3.705 + (1.5/6)*3.85 = 0.9625 + 0.375 = 5.0425
    #   saving = price gap 15.00 + 1.948667 - 5.0425 = 11.906167 -> 11.91
    row = missed(db, cfg)["FC0000003"]
    assert row["net_savings_usd"] == 11.91 and row["is_peak"] is True and row["multiplier"] == 1.17


# ------------------------------------------------------------------ Test route options: one cost, at the stop time

def test_an_option_is_priced_once_at_the_time_its_stop_happens(hub):
    db, cfg = hub
    # R01's customer is planned at 06:35, so a stop after it happens at 06:35 + 25 = 07:00, inside the morning rush.
    set_arrivals(db, "TUC-R01", {1: "06:35"})
    opts = options_of(db, cfg)
    b = opts["STN-002"]                                  # price 3.80, 2.0 mi / 6 min after stop 1
    assert (b["stop_time_local"], b["is_peak"], b["multiplier"], b["insert_after_stop"]) == ("07:00", True, 1.17, 1)
    # B: effective 6 * 1.17 = 7.02 min ; driver (7.02/60)*38 = 4.446 ; fuel (2/6)*3.8 = 1.266667 ; wear 0.50 ; detour 6.212667
    #    total 72*3.8 = 273.60 + 6.212667 = 279.812667
    assert b["extra_minutes"] == 6.0 and b["extra_minutes_effective"] == 7.0
    assert (b["fuel_cost_usd"], b["detour_cost_usd"], b["total_cost_usd"]) == (273.60, 6.21, 279.81)
    # A (baseline, priced the same way at its own stop time): effective 3 * 1.17 = 3.51 ; driver 2.223 ; fuel 0.666667 ; wear 0.25
    #    detour 3.139667 ; total 288.00 + 3.139667 = 291.139667 ; B saves 291.139667 - 279.812667 = 11.327 -> 11.33
    assert opts["STN-001"]["total_cost_usd"] == 291.14 and b["net_savings_vs_baseline_usd"] == 11.33
    # one cost per option: none of the old off-peak and rush-hour pairs are left
    assert not [k for k in b if k.endswith("_peak") and k != "is_peak" or "_peak_" in k]


def test_option_at_a_yard_stop_adds_no_unloading_time(hub):
    db, cfg = hub
    # B inserted after stop 0 (the yard, planned 09:30): no unloading time is added, so the stop is at 09:30 ; after stop 2 it is 11:00
    edit(db, "UPDATE ref_detour SET insert_after_stop = 0 WHERE route_id = 'TUC-R01' AND station_id = 'STN-002'")
    assert options_of(db, cfg)["STN-002"]["stop_time_local"] == "09:30"
    edit(db, "UPDATE ref_detour SET insert_after_stop = 2 WHERE route_id = 'TUC-R01' AND station_id = 'STN-002'")
    assert options_of(db, cfg)["STN-002"]["stop_time_local"] == "11:00"


def test_over_max_detour_is_judged_on_effective_minutes_at_the_stop_time(hub):
    db, cfg = hub
    # 13 minutes: fine at 10:25 (13 <= 15) but 13 * 1.17 = 15.21 at 07:00 (over 15)
    edit(db, "UPDATE ref_detour SET extra_minutes = 13 WHERE route_id = 'TUC-R01' AND station_id = 'STN-002'")
    assert options_of(db, cfg)["STN-002"]["over_max_detour"] is False
    set_arrivals(db, "TUC-R01", {1: "06:35"})
    opts = options_of(db, cfg)
    assert opts["STN-002"]["over_max_detour"] is True and opts["STN-002"]["extra_minutes_effective"] == 15.2
    assert opts["STN-001"]["over_max_detour"] is False


def test_over_max_option_is_flagged_and_never_the_best_stop(hub):
    db, cfg = hub
    edit(db, "UPDATE ref_detour SET extra_minutes = 20 WHERE route_id = 'TUC-R01' AND station_id = 'STN-002'")
    r = built_results(db, cfg)["detours"]["routes"]["TUC-R01"]
    opts = {o["station_id"]: o for o in r["options"]}
    assert opts["STN-002"]["over_max_detour"] is True and opts["STN-001"]["over_max_detour"] is False
    assert r["best_station_id"] == "STN-001"                    # B would have saved money, but 20 minutes is over the 15 limit
    assert r["best_station_id_incl_unverified"] != "STN-002"


def test_before_last_delivery_flag_and_penalty(hub):
    db, cfg = hub
    edit(db, "UPDATE ref_detour SET insert_after_stop = 0 WHERE route_id = 'TUC-R01' AND station_id = 'STN-002'")
    opts = options_of(db, cfg)
    assert opts["STN-002"]["before_last_delivery"] is True and opts["STN-001"]["before_last_delivery"] is False
    # penalty = 6 effective minutes * $0.50 = 3.00, shown but not part of any cost: the cost is still 273.60 + 5.566667 = 279.17
    assert opts["STN-002"]["ranking_penalty_usd"] == 3.0 and opts["STN-001"]["ranking_penalty_usd"] == 0.0
    assert opts["STN-002"]["total_cost_usd"] == 279.17


def test_default_route_is_the_one_with_the_biggest_best_saving(hub):
    db, cfg = hub
    d = built_results(db, cfg)["detours"]
    # R01: B saves 11.65 ; R03: B saves 289.725 - 278.091667 = 11.63 ; R02: S saves 2.07
    assert d["default_route_id"] == "TUC-R01"
    # Put B on R03 right on the route (no detour at all): 289.725 - 273.60 = 16.125, now the largest
    edit(db, "UPDATE ref_detour SET extra_miles = 0.0, extra_minutes = 0.0 WHERE route_id = 'TUC-R03' AND station_id = 'STN-002'")
    assert built_results(db, cfg)["detours"]["default_route_id"] == "TUC-R03"


def test_default_route_ties_go_to_the_lowest_route_id_and_a_baseline_best_counts_zero():
    none_better = {"options": [], "best_station_id": None, "baseline_station_id": None}
    at_baseline = {"options": [{"station_id": "S1", "net_savings_vs_baseline_usd": 0.0}], "best_station_id": "S1", "baseline_station_id": "S1"}
    assert detour_options.default_route_id({"TUC-R02": at_baseline, "TUC-R01": none_better}) == "TUC-R01"
    better = {"options": [{"station_id": "S2", "net_savings_vs_baseline_usd": 4.0}], "best_station_id": "S2", "baseline_station_id": "S1"}
    assert detour_options.default_route_id({"TUC-R01": none_better, "TUC-R02": better}) == "TUC-R02"
    assert detour_options.default_route_id({}) is None


def test_a_stop_without_a_planned_arrival_names_the_route(hub):
    db, cfg = hub
    set_arrivals(db, "TUC-R02", {1: None})
    with pytest.raises(ValueError, match="TUC-R02"):
        analytics.run(cfg, db_path=db)


def test_rush_hour_note_and_ranking_note_come_from_config(params):
    assert detour_options.rush_hour_note(params) == ("Rush hour, weekdays 6 to 9 am and 4 to 7 pm: detour minutes count 1.17 times "
                                                     "(Tucson Travel Time Index, Texas A&M 2025 Urban Mobility Report).")
    cfg = kit.test_cfg()
    cfg["time_cost"].update({"peak_windows": ["06:30-09:00", "11:00-14:00"], "use_reliability_buffer": True})
    assert detour_options.rush_hour_note(SavingsParams.from_config(cfg)) == (
        "Rush hour, weekdays 6:30 to 9 am and 11 am to 2 pm: detour minutes count 1.38 times "
        "(Tucson Planning Time Index, Texas A&M 2025 Urban Mobility Report).")


def test_worked_example_hand_checked_off_peak(hub):
    db, cfg = hub
    cfg["time_cost"]["worked_example_time"] = "10:00"
    w = built_results(db, cfg)["detours"]["worked_example"]
    # median basis price 3.95 ; 12 minutes at 10 am (off-peak, multiplier 1) ; 4 miles
    #   driver (12/60)*38 = 7.60 ; fuel (4/6)*3.95 = 2.633333 ; wear 4*0.25 = 1.00 ; total 11.233333 -> 11.23
    #   needed gap 11.233333 / 72 * 100 = 15.60 cents
    assert w["is_peak"] is False and w["multiplier"] == 1.0 and w["effective_minutes"] == 12.0
    assert (w["time_cost_usd"], w["fuel_cost_usd"], w["wear_cost_usd"]) == (7.6, 2.63, 1.0)
    assert w["total_usd"] == 11.23 and w["cpg_needed"] == 15.6 and w["fill_gal"] == 72
    assert w["sentence"].startswith("For a 72-gallon fill at $3.950 a gallon (median Tucson posted price, Oct 4 to Oct 31, 2026), a 12-minute detour at 10 am costs $11.23")


# ------------------------------------------------------------------ break-even at the stop time

def test_break_even_at_the_stop_time_and_the_equal_cost_property(hub):
    db, cfg = hub
    set_arrivals(db, "TUC-R01", {1: "16:30"})            # a stop after it happens at 16:55, rush hour
    set_arrivals(db, "TUC-R03", {1: "16:30"})
    con = duckdb.connect(str(db))
    params = SavingsParams.from_config(cfg)
    inputs = load_inputs(con, cfg)
    table = detour_options.break_even_table(inputs, params, cfg, ROUTES)
    # STN-002 at 3.80, detour cost per route at 16:55 (k = 1.17):
    #   R01 (2.0 mi, 6 min): 1.266667 + (7.02/60)*38 = 4.446 + 0.50 = 6.212667
    #   R03 (1.5 mi, 5 min): 0.95 + (5.85/60)*38 = 3.705 + 0.375 = 5.03     -> the lower-middle (median) route is R03
    #   gap 5.03 / 72 = 0.069861 -> 7.0 cents, with no schedule risk in it
    assert table["STN-002"] == {"cpg": 7.0, "route_id": "TUC-R03", "is_peak": True}
    # cost(actual) = cost(alternative) at the gap, with the same components (price 3.80, G = 72, multiplier 1.17)
    gap = detour_cost(3.80, 1.5, 5.0, 6.0, params, 1.17) / 72
    actual = 72 * (3.80 + gap)                           # no detour at the station already on the route
    alternative = stop_cost(72, 3.80, 1.5, 5.0, 6.0, params, 1.17)
    assert actual == pytest.approx(alternative, abs=1e-9)
    con.close()


def test_stations_geojson_has_one_break_even_with_its_route_and_time_flag(hub, tmp_path):
    from fleetfuel import export
    db, cfg = hub
    analytics.run(cfg, db_path=db)
    export.run(cfg, db_path=db, out_dir=tmp_path / "out", raw_dir=HERE / "fixtures" / "pipeline", generated_on="2026-11-17")
    fc = json.loads((tmp_path / "out" / "stations.geojson").read_text(encoding="utf-8"))
    props = {f["properties"]["station_id"]: f["properties"] for f in fc["features"]}
    # STN-002: median route R03, 4.491667 / 72 = 0.062384 -> 6.2 cents at 10:25 (off-peak)
    assert props["STN-002"]["break_even_cpg"] == 6.2 and props["STN-002"]["break_even_route_id"] == "TUC-R03"
    assert props["STN-002"]["break_even_is_peak"] is False and "break_even_cpg_peak" not in props["STN-002"]
    d = json.loads((tmp_path / "out" / "detours.json").read_text(encoding="utf-8"))
    assert d["worked_example"]["cpg_needed"] == 17.4


def test_missing_time_cost_key_raises():
    cfg = copy.deepcopy(kit.test_cfg())
    del cfg["time_cost"]["max_detour_minutes"]
    with pytest.raises(KeyError, match="time_cost.max_detour_minutes"):
        SavingsParams.from_config(cfg)


def test_the_multiplier_is_required():
    params = SavingsParams.from_config(kit.test_cfg())
    with pytest.raises(TypeError):
        stop_cost(80, 4.0, 1.0, 3.0, 6.0, params)                     # no multiplier: an error, not a silent off-peak price


def test_worked_example_uses_only_stations_inside_the_tucson_box(hub):
    db, cfg = hub
    # move A (4.00) and C (3.95) out of the Tucson box: the stations inside are B (3.80) and S (3.95), median (3.80 + 3.95) / 2 = 3.875
    edit(db, "UPDATE dim_station SET lat = 35.5 WHERE station_id IN ('STN-001', 'STN-003')")
    w = built_results(db, cfg)["detours"]["worked_example"]
    # 12 minutes at 7 am (rush hour): driver 8.892 ; fuel (4/6) * 3.875 = 2.583333 ; wear 1.00 ; total 12.475333 -> 12.48 ;
    # 12.475333 / 72 * 100 = 17.327 -> 17.3
    assert w["price_per_gal"] == 3.875 and w["fuel_cost_usd"] == 2.58 and w["total_usd"] == 12.48 and w["cpg_needed"] == 17.3


# ------------------------------------------------------------------ hours-of-service check (15 minute detour cap)

def hos(db, cfg):
    return built_results(db, cfg)["hos_check"]


def pinned_cfg():
    cfg = kit.test_cfg()
    cfg["routes"].update({"minutes_per_stop": 25, "linehaul_yard_dwell_minutes": 45})
    cfg["fueling"]["fill_minutes"] = 15
    return cfg


def test_hos_check_within_limits(hub):
    db, _ = hub
    cfg = pinned_cfg()
    edit(db, "INSERT INTO dim_route VALUES ('PHX-L01', 'PHX_DC', "
             "'[{\"seq\": 0, \"location_id\": \"PHX_DC\"}, {\"seq\": 1, \"location_id\": \"TUC_YARD\"}, {\"seq\": 2, \"location_id\": \"PHX_DC\"}]', "
             "300.0, 600.0, NULL)")
    h = hos(db, cfg)
    # the worst allowed stop is the 15 minute detour cap
    # local route (base 300 min, 1 customer): driving (300 - 25 + 15) / 60 = 4.83 ; on duty (300 + 15 + 15) / 60 = 5.5
    # linehaul (base 600 min, 0 customers, 1 middle yard stop): driving (600 - 45 + 15) / 60 = 9.5 ; on duty (600 + 15 + 15) / 60 = 10.5
    assert h == {"limit_driving_hours": 11, "limit_on_duty_hours": 14, "max_driving_hours": 9.5, "max_on_duty_hours": 10.5,
                 "routes_over": [], "message": "All routes within federal hours-of-service limits"}


def test_hos_check_flags_a_route_over_the_limit(hub):
    db, _ = hub
    cfg = pinned_cfg()
    edit(db, "UPDATE dim_route SET base_minutes = 700 WHERE route_id = 'TUC-R01'")
    h = hos(db, cfg)
    # TUC-R01: driving (700 - 25 + 15) / 60 = 11.5 hours, over the 11 hour limit ; on duty (700 + 15 + 15) / 60 = 12.17, inside 14
    assert h["routes_over"] == [{"route_id": "TUC-R01", "driving_hours": 11.5, "on_duty_hours": 12.2, "limit": "driving"}]
    assert h["message"] == "1 route over federal hours-of-service limits"
    assert h["max_driving_hours"] == 11.5 and h["max_on_duty_hours"] == 12.2
    edit(db, "UPDATE dim_route SET base_minutes = 700 WHERE route_id = 'TUC-R02'")
    assert hos(db, cfg)["message"] == "2 routes over federal hours-of-service limits"
    # base 900: driving (900 - 25 + 15) / 60 = 14.83 and on duty (900 + 15 + 15) / 60 = 15.5, both over -> "both"
    edit(db, "UPDATE dim_route SET base_minutes = 900 WHERE route_id = 'TUC-R03'")
    third = next(r for r in hos(db, cfg)["routes_over"] if r["route_id"] == "TUC-R03")
    assert third == {"route_id": "TUC-R03", "driving_hours": 14.8, "on_duty_hours": 15.5, "limit": "both"}


def test_hos_check_reaches_overview_json(hub, tmp_path):
    from fleetfuel import export
    db, _ = hub
    cfg = pinned_cfg()
    analytics.run(cfg, db_path=db)
    export.run(cfg, db_path=db, out_dir=tmp_path / "out", raw_dir=HERE / "fixtures" / "pipeline", generated_on="2026-11-17")
    o = json.loads((tmp_path / "out" / "overview.json").read_text(encoding="utf-8"))
    assert o["hos_check"]["max_driving_hours"] == 4.8 and o["hos_check"]["max_on_duty_hours"] == 5.5


def test_schedule_penalty_never_recommends_a_dearer_stop():
    """A stop that costs more in dollars never becomes the best stop, even when its schedule penalty is much lower."""
    from fleetfuel.analytics.detour_options import choose_best
    base = {"station_id": "A", "truck_access": "confirmed", "over_max_detour": False, "_total_raw": 300.0, "_penalty_raw": 5.0,
            "net_savings_vs_baseline_usd": 0.0, "extra_miles": 3.0, "extra_minutes": 10.0}
    dearer = {"station_id": "B", "truck_access": "confirmed", "over_max_detour": False, "_total_raw": 303.0, "_penalty_raw": 1.0,
              "net_savings_vs_baseline_usd": -3.0, "extra_miles": 0.3, "extra_minutes": 2.0}
    best, reason = choose_best([base, dearer], base, 72.0, confirmed_only=True)
    assert best == "A"
    assert "-" not in reason.split("$")[-1][:1]

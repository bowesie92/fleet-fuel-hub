"""v1.11 site checks (contract section 11): one cost per option, road-drawn detours, the missed-savings breakdown,
the "How this is calculated" section and the owner credit. Fixtures live in tests/fixtures/site/v111/.

The tests named test_real_data_* read site/data and pass once the orchestrator regenerates it for v1.11."""

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SITE = REPO / "site"
FIX = REPO / "tests" / "fixtures" / "site" / "v111"
REAL = SITE / "data"
PAGES = ["index", "map", "route", "missed", "hub", "assumptions"]
EM_DASH = chr(0x2014)

OPTION_FIELDS = {"station_id", "truck_access", "insert_after_stop", "stop_time_local", "is_peak", "multiplier", "extra_miles", "extra_minutes",
                 "extra_minutes_effective", "price_per_gal", "fuel_cost_usd", "detour_cost_usd", "total_cost_usd", "net_savings_vs_baseline_usd",
                 "before_last_delivery", "ranking_penalty_usd", "over_max_detour"}
REMOVED_OPTION_FIELDS = {"extra_minutes_peak", "detour_cost_peak_usd", "total_cost_peak_usd", "net_savings_vs_baseline_peak_usd", "over_max_detour_peak"}
MISSED_FIELDS = {"actual_extra_miles", "actual_extra_minutes", "actual_insert_after_stop", "alt_insert_after_stop", "is_peak", "multiplier",
                 "price_gap_usd", "actual_detour_cost_usd", "alt_detour_cost_usd"}


def text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def load(base: Path, name: str):
    return json.loads(text(base / name))


def js(name: str) -> str:
    return text(SITE / "js" / f"{name}.js")


# ---- the contract, checked against any data folder -----------------------------------------------------------


def check_detours(base: Path):
    det = load(base, "detours.json")
    assert det["default_route_id"] in det["routes"]
    assert det["rush_hour_note"] and det["ranking_note"] == "Stops before the last delivery are ranked lower to protect the delivery schedule."
    assert det["price_period_start"] and det["price_period_end"]
    for r in det["routes"].values():
        for o in r["options"]:
            assert OPTION_FIELDS <= set(o) and not (REMOVED_OPTION_FIELDS & set(o))
    we = det["worked_example"]
    assert we["sentence"] and "schedule_risk_usd" not in we
    assert {"price_period_start", "price_period_end", "price_basis"} <= set(we)


def check_stations(base: Path):
    for f in load(base, "stations.geojson")["features"]:
        p = f["properties"]
        assert "break_even_cpg_peak" not in p
        if p.get("break_even_cpg") is not None:
            assert {"break_even_route_id", "break_even_is_peak"} <= set(p)


def check_missed(base: Path):
    m = load(base, "missed.json")
    for key in ("rows", "rows_incl_unverified"):
        for r in m[key]:
            assert MISSED_FIELDS <= set(r)
            assert abs(r["net_savings_usd"] - (r["price_gap_usd"] + r["actual_detour_cost_usd"] - r["alt_detour_cost_usd"])) <= 0.011


def check_paths(base: Path):
    p = load(base, "detour_paths.geojson")
    assert p["synthetic"] is True and p["type"] == "FeatureCollection"
    for f in p["features"]:
        assert f["geometry"]["type"] == "LineString" and len(f["geometry"]["coordinates"]) >= 2
        assert {"route_id", "station_id", "insert_after_stop"} == set(f["properties"])


@pytest.mark.parametrize("check", [check_detours, check_stations, check_missed, check_paths])
def test_fixtures_follow_the_contract(check):
    check(FIX)


def test_fixture_paths_match_real_detours():
    det = load(FIX, "detours.json")
    stations = {f["properties"]["station_id"] for f in load(FIX, "stations.geojson")["features"]}
    for f in load(FIX, "detour_paths.geojson")["features"]:
        p = f["properties"]
        assert p["station_id"] in stations and p["route_id"] in det["routes"]


@pytest.mark.parametrize("check", [check_detours, check_stations, check_missed])
def test_real_data_follows_v111(check):
    """Fails only while site/data is still v1.10."""
    check(REAL)


def test_real_data_has_no_missed_paths_file():
    """Fails only while site/data is still v1.10."""
    assert not (REAL / "missed_paths.geojson").exists()


# ---- Test route -------------------------------------------------------------------------------------------------


def test_route_opens_on_default_route_and_shows_notes_once():
    r = js("route")
    assert "det.default_route_id" in r and "renderNotes" in r
    assert "det.rush_hour_note" in r and "det.ranking_note" in r
    assert 'id="notes"' in text(SITE / "route.html")
    assert r.count("renderNotes();") == 1  # called once at start (not per route), so each note shows once


def test_route_one_cost_per_option_no_peak_pairs_or_risk_chips():
    r = js("route")
    for dead in ["_peak_usd", "over_max_detour_peak", "Off-peak", "rushLabel", "schedule-risk", "peakLine"]:
        assert dead not in r, dead
    assert "stop_time_local" in r


def test_route_draws_the_road_path_and_falls_back_with_a_note():
    r = js("route")
    assert "detour_paths.geojson" in r and "pathFor(" in r and "insert_after_stop" in r
    html = text(SITE / "route.html")
    assert 'id="road-note"' in html and "straight line is drawn instead" in html
    assert "Detour (fuel stop)" in html and "straight line</div>" not in html and "Detour (fuel stop), straight" not in html
    assert "Detour lines are straight lines" not in r


# ---- Missed savings ---------------------------------------------------------------------------------------------


def test_missed_has_no_per_extra_mile_column_and_no_zero_extra_miles_text():
    m = js("missed")
    assert "Per extra mile" not in m and "perMile" not in m and "0 extra miles" not in m
    assert "actual_extra_miles" in m and "actual_extra_minutes" in m


def test_missed_shows_the_breakdown_and_both_detour_legs():
    m = js("missed")
    for token in ["Price gap x gallons ", "detour avoided at the station used ", "detour to the alternative ", " = net saving ",
                  "detour_paths.geojson", "actual_insert_after_stop", "alt_insert_after_stop"]:
        assert token in m, token
    assert "missed_paths" not in m
    assert "Detour to the station used" in text(SITE / "missed.html")


def test_missed_breakdown_arithmetic_matches_the_contract_example():
    row = load(FIX, "missed.json")["rows"][0]
    assert (row["price_gap_usd"], row["actual_detour_cost_usd"], row["alt_detour_cost_usd"], row["net_savings_usd"]) == (13.27, 19.93, 0.53, 32.67)


# ---- Station map -------------------------------------------------------------------------------------------------


def test_map_shows_one_break_even_and_the_rush_note_only_when_peak():
    m = js("map")
    assert "break_even_is_peak" in m and "rush_hour_note" in m
    assert "break_even_cpg_peak" not in m and "off-peak" not in m.lower()


# ---- Overview ------------------------------------------------------------------------------------------------------


def test_overview_scout_wording_and_how_link():
    html, i = text(SITE / "index.html"), js("index")
    assert "captured" not in html and "per gallon captured" not in i
    assert "saved per gallon moved to this station" in html and "saved per gallon moved to this station" in i
    assert "Already available at posted prices" not in html + i and "scout-already" not in html + i
    assert "scout-foot" not in html + i
    assert '<a href="assumptions.html#how">How this is calculated</a>' in html


# ---- Assumptions "How this is calculated" -------------------------------------------------------------------------


def test_assumptions_how_section():
    html, a = text(SITE / "assumptions.html"), js("assumptions")
    assert 'id="how"' in html and "How this is calculated" in html
    assert 'id="how-notes"' in html and 'id="how-missed"' in html and 'id="how-scout"' in html
    for token in ["missed.json", "partner_scout.json", "detours.json", "rush_hour_note", "ranking_note", "Already available at posted prices",
                  "annual_savings_without_deal_usd", "sc.method", "price_gap_usd"]:
        assert token in a, token
    how = html.split('id="how"')[1].split('id="help"')[0]
    assert "Synthetic prices built from the EIA weekly diesel average (benchmark source: U.S. Energy Information Administration)" in how


# ---- credit, banner, rules ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", PAGES)
def test_sidebar_credit_and_banner(name):
    html = text(SITE / f"{name}.html")
    side = html.split('<aside class="sidebar">')[1].split("</aside>")[0]
    assert "Built by Cass Bowes" in side and 'href="https://github.com/bowesie92"' in side
    assert "linkedin" not in html.lower()
    assert "SYNTHETIC DATA" in html and 'href="assumptions.html"' in html


def test_fixtures_have_no_em_dashes():
    for p in FIX.glob("*"):
        assert EM_DASH not in text(p), p.name


def test_no_hard_coded_colours_in_new_css():
    import re

    pattern = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")
    assert not pattern.search(text(SITE / "css" / "extra.css"))

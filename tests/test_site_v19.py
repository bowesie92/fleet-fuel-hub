"""v1.9 site checks: time-cost display on the Test route and map, unit formatting, source links, attributions."""

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SITE = REPO / "site"
FIXTURES = REPO / "tests" / "fixtures" / "site" / "v19"

OSM_LINK = '<a href="https://www.openstreetmap.org/copyright"'
CC_LINK = '<a href="https://creativecommons.org/licenses/by-sa/4.0/"'
ORS_CREDIT = "© openrouteservice by HeiGIT | Data from "
ODBL_LINE = "Station locations: Data from "


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def page(name: str) -> str:
    return text(SITE / f"{name}.html")


def js(name: str) -> str:
    return text(SITE / "js" / f"{name}.js")


# ---- Test route -----------------------------------------------------------------------------------------------


def test_worked_example_callout():
    assert 'id="worked"' in page("route")
    r = js("route")
    assert "Worked example" in r and "we.sentence" in r
    for field in ["effective_minutes", "time_cost_usd", "fuel_cost_usd", "cpg_needed", "detour_minutes"]:
        assert field in r
    assert "worked_example" in r


def test_peak_and_off_peak_lines_and_chips():
    r = js("route")
    assert "stop_time_local" in r and "Costed at " in r  # one cost per option, at its own stop time (v1.11)
    assert "net_savings_vs_baseline_peak_usd" not in r and "Off-peak: " not in r
    assert "schedule-risk charge" not in r
    assert "o.over_max_detour" in r and "detour limit" in r
    assert "-minute " in r  # "Over the 45-minute detour limit", the limit read from meta
    assert "time_cost.max_detour_minutes" in r and "FFH.loadOptional('meta.json')" in r


def test_over_limit_options_are_greyed_out():
    assert ".optlist .opt.over" in text(SITE / "css" / "extra.css")
    assert "aria-disabled" in js("route")


def test_map_panel_shows_both_break_evens():
    m = js("map")
    assert "break_even_is_peak" in m and "rush_hour_note" in m and "break_even_cpg_peak" not in m
    assert 'id="p-be-peak"' in page("map")


def test_v19_fixtures_have_the_new_fields():
    det = json.loads(text(FIXTURES / "detours.json"))
    we = det["worked_example"]
    assert {"sentence", "effective_minutes", "time_cost_usd", "fuel_cost_usd", "cpg_needed"} <= set(we)
    opts = [o for r in det["routes"].values() for o in r["options"]]
    assert all({"before_last_delivery", "over_max_detour"} <= set(o) for o in opts)
    assert any(o["over_max_detour"] for o in opts) and any(o["before_last_delivery"] for o in opts)


# ---- units and links in the assumptions ---------------------------------------------------------------------


def test_time_cost_units_are_formatted():
    c = js("common")
    for token in ["_time_index$", "'×'", "_usd_per_minute$", "'/minute'", "' minutes'", "_mpg$", "peakWindows"]:
        assert token in c, token


def test_source_urls_become_links_with_source_names():
    c = js("common")
    assert "function linkify" in c
    assert "FHWA Urban Congestion Report" in c and "ops.fhwa.dot.gov" in c
    assert "linkify(txt)" in c  # the panel renders long values and notes through it
    a = js("assumptions")
    assert "FFH.linkify(" in a  # the full page renders values and notes through it
    assert "time_cost.index_cross_check" not in a  # shown like any other entry, so it needs no special case


def test_fixture_meta_has_time_cost_entries_and_cross_check():
    meta = json.loads(text(FIXTURES / "meta.json"))
    by = {a["key"]: a for a in meta["assumptions"]}
    assert by["time_cost.travel_time_index"]["value"] == 1.07
    assert not [k for k in by if "overtime" in k]
    assert "https://ops.fhwa.dot.gov" in by["time_cost.travel_time_index"]["note"]
    assert "time_cost.index_cross_check" in by
    head = {a["key"] for a in meta["headline_assumptions"]}
    assert {"time_cost.travel_time_index", "time_cost.peak_windows", "time_cost.max_detour_minutes"} <= head


# ---- attributions (DATA_LICENSES.md); never merged into the EIA captions or the banner ---------------------------------


def credit(html: str) -> list[str]:
    return re.findall(r'<p class="foot attrib ors"[^>]*>(.*?)</p>', html)


@pytest.mark.parametrize("name", ["index", "route", "map", "missed", "assumptions"])
def test_routing_credit_wherever_routing_results_appear(name):
    found = credit(page(name))
    assert found, name
    for c in found:
        assert ORS_CREDIT in c and OSM_LINK in c and CC_LINK in c
        assert "Routing results licensed" in c and "CC BY-SA 4.0" in c


def test_routing_credit_sits_with_the_missed_table_and_comparison_map():
    html = page("missed")
    assert len(credit(html)) == 2
    assert "attrib ors" in html.split('id="rows"')[1].split("</section>")[0]
    assert "attrib ors" in html.split('id="cmp"')[1].split("</section>")[0]


def test_route_page_credit_is_in_the_panel_head_and_map_credit_in_the_panel():
    assert "attrib ors" in page("route").split('id="route-select"')[1].split('id="p-metrics"')[0]
    assert "attrib ors" in page("map").split('id="panel"')[1]


@pytest.mark.parametrize("name", ["hub", "assumptions"])
def test_odbl_line_on_hub_and_assumptions(name):
    m = re.findall(r'<p class="foot attrib odbl">(.*?)</p>', page(name))
    assert m, name
    assert ODBL_LINE in m[0] and OSM_LINK in m[0] and m[0].rstrip().endswith("available under the ODbL")


def test_attributions_never_mixed_with_eia_captions_or_the_banner():
    for name in ["index", "map", "route", "missed", "hub", "assumptions"]:
        html = page(name)
        for note in re.findall(r'<p class="foot eia-note"[^>]*>(.*?)</p>', html):
            assert "openrouteservice" not in note and "OpenStreetMap" not in note
        banner = html.split("</header>")[0] if "</header>" in html else html[:3000]
        strip = html.split('class="strip"')[1].split("</div>")[0] if 'class="strip"' in html else ""
        assert "openrouteservice" not in strip and "ODbL" not in strip


def test_data_licences_link_and_offline_text():
    for name in ["index", "map", "route", "missed", "hub", "assumptions"]:
        assert 'href="assumptions.html#licences"' in page(name), name
    a = page("assumptions")
    assert 'id="licences"' in a and "Data licences" in a and "DATA_LICENSES.md" in a
    assert "OpenFreeMap" in a and "OpenMapTiles" in a and "ODbL" in a and "CC BY-SA 4.0" in a


def test_every_map_keeps_its_attribution_control():
    common = js("common")
    assert common.count("new maplibregl.Map(") == 1  # every page builds its map through FFH.createMap
    assert "attributionControl: { compact: true }" in common
    for p in SITE.rglob("*.js"):
        assert "attributionControl: false" not in text(p)
    for name in ["index", "map", "route", "missed"]:
        assert "FFH.createMap(" in js(name)
    assert ".maplibregl-ctrl-attrib" not in text(SITE / "css" / "app.css") + text(SITE / "css" / "extra.css")
    assert "https://tiles.openfreemap.org/styles/liberty" in common


def test_overview_routing_credit_is_its_own_paragraph_beside_scout_and_tiles():
    html = page("index")
    assert len(credit(html)) == 2
    assert "attrib ors" in html.split('id="scout"')[1].split("</section>")[0]
    assert "attrib ors" in html.split('id="kpis"')[1].split('class="grid"')[0]
    for note in re.findall(r'<p class="foot eia-note"[^>]*>(.*?)</p>', html):
        assert "openrouteservice" not in note


def test_worked_example_shows_time_fuel_and_wear():
    r = js("route")
    assert "'wear ' + FFH.usd(we.wear_cost_usd)" in r and "we.time_cost_usd" in r and "we.fuel_cost_usd" in r


def test_rush_hour_figure_flags_over_limit_options():
    r = js("route")
    assert "(rush hour)" in r and "o.is_peak" in r and "o.over_max_detour" in r  # v1.11: one cost, judged at the stop time


SOURCES = {"bls.gov": "BLS Occupational Outlook", "nacfe.org": "NACFE Run on Less Regional",
           "truckingresearch.org": "ATRI Operational Costs of Trucking 2026", "ecfr.gov": "eCFR 49 CFR 395"}


def test_no_trace_of_overtime_on_the_site():
    for p in list(SITE.rglob("*.js")) + list(SITE.rglob("*.html")) + list(SITE.rglob("*.css")):
        assert "overtime" not in text(p).lower(), p.name


def test_source_links_have_readable_names():
    c = js("common")
    for host, name in SOURCES.items():
        assert f"'{host}': '{name}'" in c, host
    assert "replace(/^www\\./, '')" in c  # www.bls.gov and www.ecfr.gov map to the same names
    assert "as reported, not checked by this project" in text(FIXTURES / "meta.json")
    # the panel and the page show the whole note, not a cut-down one
    assert "slice(0" not in js("assumptions") and "substring(" not in js("assumptions")


def test_mpg_and_hours_formats():
    c = js("common")
    assert "' mpg'" in c and "v.toFixed(1)" in c and "' hours'" in c
    meta = json.loads(text(FIXTURES / "meta.json"))
    head = {a["key"] for a in meta["headline_assumptions"]}
    assert {"fleet.local_mpg", "fleet.linehaul_mpg", "hos.max_driving_hours", "hos.max_on_duty_hours"} <= head


def test_hours_of_service_card_on_overview_and_test_route():
    assert 'id="hos"' in page("index") and 'id="hos"' in page("route")
    assert "FFH.renderHos(" in js("index") and "FFH.renderHos(" in js("route")
    c = js("common")
    assert "function renderHos" in c and "hos.message" in c and "Longest route day: " in c
    assert "limits ' + hos.limit_driving_hours + ' h and ' + hos.limit_on_duty_hours + ' h, FMCSA)" in c
    assert "routes_over" in c and "' warn'" in c
    css = text(SITE / "css" / "extra.css")
    assert ".hos.warn" in css and "var(--amber-" in css.split(".hos.warn")[1].split("}")[0]
    ok = json.loads(text(FIXTURES / "overview.json"))["hos_check"]
    assert ok["routes_over"] == [] and ok["message"] == "All routes within federal hours-of-service limits"
    bad = json.loads(text(FIXTURES / "overview_hos_over.json"))["hos_check"]
    assert bad["message"] == "1 route over federal hours-of-service limits"
    assert set(bad["routes_over"][0]) == {"route_id", "driving_hours", "on_duty_hours", "limit"}
    assert bad["routes_over"][0]["limit"] in {"driving", "on_duty", "both"}
    # every field is read, and a missing one is skipped rather than printed as undefined or NaN
    assert "function hosRouteText" in c
    for token in ["r.driving_hours", "r.on_duty_hours", "r.route_id", "r.limit", "on_duty: 'over the on-duty limit'", "both: 'over both limits'"]:
        assert token in c, token
    body = c.split("function hosRouteText")[1].split("function renderHos")[0]
    assert body.count("isNum(") >= 2 and "undefined" not in body

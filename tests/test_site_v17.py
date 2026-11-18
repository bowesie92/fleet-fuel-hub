"""v1.7 site checks: rates lead, partner tile, derived assumptions, and the two EIA captions (CLAUDE.md rule 11)."""

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SITE = REPO / "site"
FIXTURES = REPO / "tests" / "fixtures" / "site" / "v17"

EIA_SOURCE = "Source: U.S. Energy Information Administration (EIA)"
SYN_LINE = "Synthetic prices built from the EIA weekly diesel average (benchmark source: U.S. Energy Information Administration)"
OLD_LINE = "Synthetic prices built from the EIA weekly average."


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def page(name: str) -> str:
    return text(SITE / f"{name}.html")


def notes(html: str, cls: str) -> list[str]:
    return re.findall(rf'<p class="foot {cls}"[^>]*>(.*?)</p>', html)


# ---- the synthetic-prices line: every element that shows synthetic prices ---------------------------------------

PRICE_ELEMENTS = {
    "index": ["scout", "kpis", "trend"],  # scout card (cpg headline), KPI tiles, trend chart
    "map": ["legend", "p-chip", "chart"],  # legend (JS), below-regional-avg chip, station chart
    "route": ["optlist"],  # route options
    "missed": ["hero-cpg", "rows", "cmp"],  # cents headline, table, comparison card
    "hub": ["sources", "clean"],  # samples and unified table
}


@pytest.mark.parametrize("name", list(PRICE_ELEMENTS))
def test_every_page_with_prices_has_the_synthetic_line_and_never_the_old_one(name):
    html = page(name)
    found = notes(html, "eia-note")
    assert found, name
    assert all(n == SYN_LINE for n in found), (name, found)
    assert OLD_LINE not in html
    assert "endorse" not in html.lower() and "approved by" not in html.lower()


def section_of(html: str, marker: str, end: str = "</section>") -> str:
    return html.split(marker, 1)[1].split(end, 1)[0]


def test_overview_tiles_scout_and_trend_carry_the_synthetic_line():
    html = page("index")
    scout = section_of(html, 'id="scout"')
    assert SYN_LINE in scout  # scout card, cents per captured gallon headline
    tiles_note = html.split('id="kpis"')[1].split("</p>")[0]
    assert "eia-note" in tiles_note and SYN_LINE in tiles_note  # KPI tiles, cents per card gallon headline
    trend = section_of(html, "Weekly diesel price trend")
    assert SYN_LINE in trend


def test_hub_samples_and_table_carry_the_synthetic_line():
    html = page("hub")
    assert SYN_LINE in section_of(html, 'id="h-src"')
    assert SYN_LINE in section_of(html, 'id="clean"')


def test_missed_hero_table_and_comparison_carry_the_synthetic_line():
    html = page("missed")
    assert SYN_LINE in section_of(html, 'id="hero-cpg"')
    assert SYN_LINE in section_of(html, 'id="rows"')
    assert SYN_LINE in section_of(html, 'id="cmp"')


def test_route_options_carry_the_synthetic_line():
    html = page("route")
    assert SYN_LINE in html.split('id="route-select"')[1].split('id="p-metrics"')[0]


def test_map_legend_chip_and_station_chart_carry_the_synthetic_line():
    html = page("map")
    chip = html.index('id="p-chip"')
    assert SYN_LINE in html[chip : chip + 500]  # "below regional avg" chip
    chart = html.split('id="chart"')[1]
    assert SYN_LINE in chart  # station price block chart
    map_js = text(SITE / "js" / "map.js")
    legend = map_js.split("function renderLegend")[1]
    assert "FFH.eiaNote()" in legend  # map legend (built in JS)
    common = text(SITE / "js" / "common.js")
    assert "function eiaNote" in common and "SYN_CAPTION" in common and SYN_LINE in common


# ---- the plain source line: only on charts that plot the actual EIA series ----------------------------------------


def test_plain_source_line_only_on_the_two_eia_series_charts():
    for name in ["index", "map"]:
        html = page(name)
        assert notes(html, "eia-src") == [EIA_SOURCE], name
        assert html.count(EIA_SOURCE) == 1, name
    # index: the line sits in the weekly trend card, beside the chart that has the EIA series
    assert EIA_SOURCE in section_of(page("index"), "Weekly diesel price trend")
    # map: it sits with the station's price-vs-EIA chart
    assert EIA_SOURCE in page("map").split('id="chart"')[1]
    for name in ["route", "missed", "hub", "assumptions"]:
        assert EIA_SOURCE not in page(name), name


def test_charts_with_both_series_carry_both_lines():
    for name, marker in [("index", "Weekly diesel price trend"), ("map", 'id="chart"')]:
        html = page(name)
        block = section_of(html, marker) if name == "index" else html.split(marker)[1]
        assert SYN_LINE in block and EIA_SOURCE in block, name


def test_plain_source_line_not_in_js_copy():
    for js in (SITE / "js").glob("*.js"):
        body = text(js)
        if js.name == "common.js":
            continue  # the constant is defined once and not rendered anywhere else
        assert EIA_SOURCE not in body, js.name
    common = text(SITE / "js" / "common.js")
    assert common.count(EIA_SOURCE) == 1
    assert "eiaNote" in common and "return el('p', 'foot eia-note', SYN_CAPTION)" in common


# ---- rates lead -----------------------------------------------------------------------------------------------


def test_rates_lead_on_overview_and_missed():
    index = page("index")
    assert 'id="scout-cpg"' in index and "saved per gallon moved to this station" in index
    assert 'id="scout-annual"' in index and 'id="scout-truck"' in index
    ijs = text(SITE / "js" / "index.js")
    assert "net_savings_cpg_captured" in ijs
    assert "missed_savings_cpg_card" in ijs and "per card gallon" in ijs
    assert "missed_usd_per_truck_per_year" in ijs and "annual_net_savings_per_truck_usd" in ijs
    missed = page("missed")
    assert 'id="hero-cpg"' in missed and "per card gallon" in missed
    assert 'id="hero-week"' in missed and 'id="hero-truck"' in missed
    mjs = text(SITE / "js" / "missed.js")
    assert "cpg_per_card_gallon" in mjs  # picks the _incl_unverified variant with the toggle
    assert "usd_per_truck_per_year" in mjs


def test_partner_tile_relabelled_and_linked_to_the_scout():
    js = text(SITE / "js" / "index.js")
    assert "Tucson card gallons at partner pricing" in js
    assert "tucson_card_gallons_partner_share" in js
    assert "href: '#scout'" in js
    assert "Partner share of card gallons" not in js
    assert "countPartners" in js and "partnerNote" in js  # the zero-case sentence is derived from the data
    assert "where all card fills happen" not in js  # no hard-coded claim


def test_assumptions_derived_entry_and_chip():
    common = text(SITE / "js" / "common.js")
    assert "Tucson local route length" in common and "mi/day" in common
    assert "source === 'derived'" in common
    assert "derived from the data'" not in common  # v1.17: the badge is gone
    assert "derived from the data'" not in text(SITE / "js" / "assumptions.js")


def test_assumption_units_and_copy_fixes():
    common = text(SITE / "js" / "common.js")
    for token in ["per_hour", "per_mile", "_per_gal", "_cpg", "_rate", "_mi", "_usd", "'Yes'", "'No'"]:
        assert token in common
    assert "driver time and wear" in text(SITE / "js" / "map.js")
    assert "Whole period: " in text(SITE / "js" / "missed.js")
    index = text(SITE / "js" / "index.js")
    assert "card gallons vs prior 4 weeks" in index
    assert "avg_yard_price_per_gal" in index


def test_fixtures_have_v17_shapes():
    kpis = json.loads(text(FIXTURES / "overview.json"))["kpis"]
    assert {"missed_savings_cpg_card", "tucson_card_gallons_partner_share"} <= set(kpis)
    assert "partner_share_gallons" not in kpis
    missed = json.loads(text(FIXTURES / "missed.json"))
    assert {"card_gallons", "cpg_per_card_gallon", "cpg_per_card_gallon_incl_unverified"} <= set(missed)
    scout = json.loads(text(FIXTURES / "partner_scout.json"))
    assert all("net_savings_cpg_captured" in c for s in scout["stations"] for c in s["curve"])
    meta = json.loads(text(FIXTURES / "meta.json"))
    derived = [a for a in meta["headline_assumptions"] if a.get("source") == "derived"]
    assert [a["key"] for a in derived] == ["derived.tucson_local_route_miles_per_day"]
    assert all(a["source"] in {"config", "derived"} for a in meta["headline_assumptions"])


# ---- QA fixes -------------------------------------------------------------------------------------------------


def test_missed_weekly_chart_carries_the_synthetic_line():
    card = section_of(page("missed"), 'id="weekly"')
    assert SYN_LINE in card


def test_assumptions_page_does_not_credit_prices_to_eia():
    html = page("assumptions")
    assert "Station locations and diesel prices come from public sources" not in html
    assert "Station locations come from OpenStreetMap. Diesel prices are synthetic, built from the EIA weekly diesel average" in html
    assert "benchmark source: U.S. Energy Information Administration" in html


def test_zero_partner_note_uses_the_configured_tucson_box_not_city():
    js = text(SITE / "js" / "index.js")
    body = js.split("function countPartners")[1].split("function partnerNote")[0]
    assert "fetch.overpass.tucson_metro_bbox" in body and "city" not in body


def test_map_keeps_synthetic_line_when_price_has_no_eia_week():
    assert "chipSrc.hidden = !FFH.isNum(paid4)" in text(SITE / "js" / "map.js")


def test_partner_program_label_and_derived_entry_on_full_page():
    assert "'Fuel partner program'" in text(SITE / "js" / "common.js")
    a = text(SITE / "js" / "assumptions.js")
    assert "headline_assumptions" in a and "derived from the data'" not in a and "keyLabel" in a


def test_fixture_meta_matches_the_current_partner_assumptions():
    keys = {a["key"] for a in json.loads(text(FIXTURES / "meta.json"))["assumptions"]}
    assert "prices.partner_count" not in keys
    assert {"prices.partner_set_description", "prices.partner_set_bbox", "fetch.overpass.tucson_metro_bbox"} <= keys


def test_derived_entries_keep_their_note_in_the_description_not_after_the_badge():
    common = text(SITE / "js" / "common.js")
    assert "' (' + derivedNote(a.note) + ')'" not in common
    assert "derivedNote(a.note || '')" in common
    a = text(SITE / "js" / "assumptions.js")
    assert "' (' + FFH.derivedNote(a.note) + ')'" not in a
    assert "FFH.derivedNote(a.note || '')" in a
    meta = json.loads(text(FIXTURES / "meta.json"))
    d = [x for x in meta["headline_assumptions"] if x["key"] == "derived.tucson_local_route_miles_per_day"][0]
    assert f"{d['value']} mi/day" == "31.6 mi/day" and d["note"].startswith("synthetic-route assumption")


def test_zero_partner_note_grammar_for_one_two_and_many():
    body = text(SITE / "js" / "index.js").split("function noneInTucson")[1].split("function partnerNote")[0]
    assert "'The 1 partner station is not in Tucson,'" in body
    assert "'Neither of the 2 partner stations is in Tucson,'" in body
    assert "'None of the ' + n + ' partner stations is in Tucson,'" in body
    note = text(SITE / "js" / "index.js").split("function partnerNote")[1].split("/**")[0]
    assert "noneInTucson(c.total) + ' so no Tucson card gallons get partner pricing.'" in note

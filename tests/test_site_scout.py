"""Site checks for the Overview partnership scout card (contract 7, partner_scout.json)."""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SITE = REPO / "site"
FIXTURE = REPO / "tests" / "fixtures" / "site" / "partner_scout.json"
EM_DASH = chr(0x2014)


def scout() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_scout_card_markup_on_index():
    html = (SITE / "index.html").read_text(encoding="utf-8")
    for needle in ['id="scout"', 'id="scout-slider"', 'id="scout-rows"', 'id="scout-line"', 'class="callout"', "If a Tucson station became a fuel partner"]:
        assert needle in html
    assert html.index('id="scout"') < html.index('id="kpis"')
    assert html.index('id="scout"') > html.index("<h1>Overview</h1>")


def test_synthetic_scenario_label_present():
    html = (SITE / "index.html").read_text(encoding="utf-8")
    assert "Synthetic scenario" in html
    assert scout()["scenario_label"] == "Synthetic scenario"


def test_fixture_matches_contract_shape():
    d = scout()
    for key in ["synthetic", "scenario_label", "scope", "discount_steps_cpg", "default_discount_cpg", "best_station_id_at_default", "method", "stations"]:
        assert key in d
    assert d["synthetic"] is True
    steps = d["discount_steps_cpg"]
    assert steps and d["default_discount_cpg"] in steps
    point_keys = {"discount_cpg", "fills_existing", "gallons_existing", "fills_shifted", "gallons_shifted", "gallons_per_month", "annual_net_savings_usd"}
    for s in d["stations"]:
        assert {"station_id", "display_name", "lat", "lon", "curve"} <= set(s)
        assert [c["discount_cpg"] for c in s["curve"]] == steps
        for c in s["curve"]:
            assert point_keys <= set(c)

    def at_default(s):
        return next(c for c in s["curve"] if c["discount_cpg"] == d["default_discount_cpg"])["annual_net_savings_usd"]

    ranked = [at_default(s) for s in d["stations"]]
    assert ranked == sorted(ranked, reverse=True)
    assert d["stations"][0]["station_id"] == d["best_station_id_at_default"]


def test_slider_bounds_come_from_data_not_markup():
    js = (SITE / "js" / "index.js").read_text(encoding="utf-8")
    assert "discount_steps_cpg" in js and "default_discount_cpg" in js
    assert "slider.max = steps.length - 1" in js
    slider_tag = (SITE / "index.html").read_text(encoding="utf-8").split('id="scout-slider"')[1].split(">")[0]
    assert "min=" not in slider_tag and "max=" not in slider_tag and "value=" not in slider_tag


def test_scout_copy_is_net_of_detours_and_missing_file_is_safe():
    js = (SITE / "js" / "index.js").read_text(encoding="utf-8")
    assert "after detour costs" in js and "assumptions.html#how" in (SITE / "index.html").read_text(encoding="utf-8")
    assert "loadOptional('partner_scout.json')" in js
    assert 'id="scout"' in (SITE / "index.html").read_text(encoding="utf-8")
    assert "hidden" in (SITE / "index.html").read_text(encoding="utf-8").split('id="scout"')[1].split(">")[0]
    assert EM_DASH not in FIXTURE.read_text(encoding="utf-8")


def test_route_selector_is_tucson_local_only():
    js = (SITE / "js" / "route.js").read_text(encoding="utf-8")
    assert "indexOf('TUC-R') === 0" in js

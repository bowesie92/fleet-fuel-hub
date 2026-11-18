"""v1.6 site checks: yard/card split, per-truck figures, assumptions panel, Node 24 workflow actions."""

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SITE = REPO / "site"
FIXTURES = REPO / "tests" / "fixtures" / "site" / "v16"
WORKFLOWS = REPO / ".github" / "workflows"

# Majors that run on Node 24 (checked with `gh api repos/<owner>/<repo>/releases/latest` and each action.yml).
ACTION_VERSIONS = {
    "actions/checkout": "v7",
    "astral-sh/setup-uv": "v10.3.3",
    "actions/configure-pages": "v6",
    "actions/upload-pages-artifact": "v5",
    "actions/deploy-pages": "v5",
}
NODE20_ERA = {
    "actions/checkout": {"v1", "v2", "v3", "v4"},
    "astral-sh/setup-uv": {"v1", "v2", "v3", "v4", "v5"},
    "actions/configure-pages": {"v1", "v2", "v3", "v4", "v5"},
    "actions/upload-pages-artifact": {"v1", "v2", "v3"},
    "actions/deploy-pages": {"v1", "v2", "v3", "v4"},
}


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["index", "missed"])
def test_assumptions_panel_exists_and_reads_headline_assumptions(name):
    html = text(SITE / f"{name}.html")
    assert 'id="assump-panel"' in html and 'id="assump-list"' in html
    assert "Assumptions behind these numbers" in html
    assert 'href="assumptions.html"' in html.split('id="assump-panel"')[1]
    assert "headline_assumptions" in text(SITE / "js" / "common.js")
    assert "renderAssumptionsPanel" in text(SITE / "js" / ("index.js" if name == "index" else "missed.js"))


def test_panel_hides_when_field_is_missing():
    assert "!list || !list.length) { return; }" in text(SITE / "js" / "common.js")
    assert 'id="assump-panel" hidden' in text(SITE / "index.html")
    assert 'id="assump-panel" hidden' in text(SITE / "missed.html")


def test_per_truck_elements_exist():
    assert 'id="hero-truck"' in text(SITE / "missed.html")
    assert 'id="scout-truck"' in text(SITE / "index.html")
    missed = text(SITE / "js" / "missed.js")
    assert "usd_per_truck_per_year" in missed and "perTruckText" in missed
    index = text(SITE / "js" / "index.js")
    assert "annual_net_savings_per_truck_usd" in index
    assert "missed_usd_per_truck_per_year" in index
    assert "per truck per year" in text(SITE / "js" / "common.js")


def test_yard_and_card_split_markup():
    js = text(SITE / "js" / "index.js")
    assert "card_gallons" in js and "yard_gallons" in js
    assert "Phoenix DC tank" in js
    assert "Card fills only" in js
    assert "Tucson card gallons at partner pricing" in js
    assert "'split'" in js


def test_fixtures_have_v16_shapes():
    meta = json.loads(text(FIXTURES / "meta.json"))
    assert meta["trucks_in_fleet"] == 20 and meta["headline_assumptions"]
    assert all({"key", "value", "note"} <= set(a) for a in meta["headline_assumptions"])
    kpis = json.loads(text(FIXTURES / "overview.json"))["kpis"]
    assert kpis["card_gallons"] + kpis["yard_gallons"] == kpis["total_gallons"]
    missed = json.loads(text(FIXTURES / "missed.json"))
    assert {"trucks_in_fleet", "usd_per_truck_per_year", "usd_per_truck_per_year_incl_unverified"} <= set(missed)
    scout = json.loads(text(FIXTURES / "partner_scout.json"))
    assert scout["trucks_in_fleet"] == 20
    assert all("annual_net_savings_per_truck_usd" in c for s in scout["stations"] for c in s["curve"])


@pytest.mark.parametrize("name", ["ci.yml", "pages.yml"])
def test_workflows_use_node24_action_versions(name):
    body = text(WORKFLOWS / name)
    found = re.findall(r"uses:\s*([\w./-]+)@([\w.]+)", body)
    assert found
    for action, ref in found:
        assert ref == ACTION_VERSIONS[action], (action, ref)
        assert ref not in NODE20_ERA[action]
    if name == "pages.yml":
        for action in ["actions/configure-pages", "actions/upload-pages-artifact", "actions/deploy-pages"]:
            assert f"{action}@{ACTION_VERSIONS[action]}" in body

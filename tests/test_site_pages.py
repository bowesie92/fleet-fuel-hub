"""Site checks: every page has the shell and banner, data files match the contract, style rules hold."""

import json
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SITE = REPO / "site"
DATA = SITE / "data"
PAGES = ["index", "map", "route", "missed", "hub", "assumptions", "guide"]
NAV_ORDER = ["Start here", "Overview", "Station map", "Test route", "Missed savings", "Data hub"]
EIA = "Source: U.S. Energy Information Administration (EIA)"
EM_DASH = chr(0x2014)
FORBIDDEN_TEXT = ["FuelOptix", "FleetFuel ", "Arizona Fuel Insights", "Current price", "Last updated"]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def page(name: str) -> str:
    return read(SITE / f"{name}.html")


@pytest.mark.parametrize("name", PAGES)
def test_page_has_banner_and_assumptions_link(name):
    html = page(name)
    assert "SYNTHETIC DATA" in html
    assert 'href="assumptions.html"' in html


@pytest.mark.parametrize("name", PAGES)
def test_page_has_app_shell(name):
    html = page(name)
    assert 'class="strip"' in html
    assert "Demonstration only. Not real data." in html
    assert "Fleet Fuel Hub" in html and "Fuel analytics demo" in html
    assert "Data as of" in html and "Synthetic data, rebuilt on each deploy" in html
    sidebar = html.split('<aside class="sidebar">')[1].split("</aside>")[0]
    positions = [sidebar.index(f"> {label}</a>") for label in NAV_ORDER]
    assert positions == sorted(positions)
    assert "Drivers" not in sidebar
    tabbar = html.split('class="tabbar"')[1].split("</nav>")[0]
    for label in ["Overview", "Map", "Route", "Savings", "Hub"]:
        assert f"<span>{label}</span>" in tabbar


def test_help_section_exists():
    assert 'id="help"' in page("assumptions")


def test_no_em_dashes_in_site():
    for path in SITE.rglob("*"):
        if path.is_file() and path.suffix in {".html", ".css", ".js", ".json", ".geojson"}:
            assert EM_DASH not in read(path), path.name


@pytest.mark.parametrize("name", PAGES)
def test_no_forbidden_mockup_text(name):
    html = page(name)
    for term in FORBIDDEN_TEXT:
        assert term not in html, term
    for js in (SITE / "js").glob("*.js"):
        text = read(js)
        for term in FORBIDDEN_TEXT:
            assert term not in text, (js.name, term)


def test_eia_source_line_on_pages_that_draw_eia_prices():
    for name in ["index", "map"]:
        assert EIA in page(name), name
    assert EIA in read(SITE / "js" / "common.js")
    for name in PAGES:
        assert not list(SITE.glob("**/*eia*.png")) and "eia-logo" not in page(name).lower()


def test_pages_with_eia_chart_code_carry_the_caption():
    for name in PAGES:
        scripts = [s for s in re.findall(r'src="js/(\w+)\.js"', page(name)) if s != "common"]
        uses_eia = any("price_history" in read(SITE / "js" / f"{s}.js") for s in scripts)
        if uses_eia:
            assert EIA in page(name), name


def test_charts_have_footnotes_marked_synthetic():
    for name in ["index", "map", "missed"]:
        html = page(name)
        assert html.count("canvas") >= 2
        assert "(synthetic)" in html


def test_tokens_match_style_guide():
    guide = read(REPO / "docs" / "design" / "STYLE.md")
    block = guide.split("## 2. Design tokens")[1].split("```css")[1].split("```")[0]
    expected = dict(re.findall(r"(--[\w-]+):\s*([^;]+);", block))
    actual = dict(re.findall(r"(--[\w-]+):\s*([^;]+);", read(SITE / "css" / "tokens.css")))
    assert expected
    norm = lambda d: {k: re.sub(r"\s+", "", v) for k, v in d.items()}  # noqa: E731
    assert norm(actual) == norm(expected)


def test_no_color_literals_outside_tokens():
    pattern = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")
    files = [p for p in (SITE / "css").glob("*.css") if p.name != "tokens.css"] + list((SITE / "js").glob("*.js"))
    for path in files:
        assert not pattern.search(read(path)), path.name
    for name in PAGES:
        assert not pattern.search(page(name)), name


def test_cdn_versions_are_pinned():
    for name in ["index", "map", "route", "missed"]:
        html = page(name)
        for lib in re.findall(r"cdn\.jsdelivr\.net/npm/([\w.-]+)@([^/]+)/", html):
            assert re.fullmatch(r"\d+\.\d+\.\d+", lib[1]), lib
    assert "https://tiles.openfreemap.org/styles/liberty" in read(SITE / "js" / "common.js")


def load(name: str):
    return json.loads(read(DATA / name))


@pytest.mark.parametrize("name", ["meta.json", "overview.json", "price_history.json", "detours.json", "missed.json",
                                  "hub.json", "stations.geojson", "routes.geojson", "detour_paths.geojson"])
def test_data_file_parses_and_is_synthetic(name):
    if not (DATA / name).exists():
        assert name == "detour_paths.geojson"  # the only data file the site can do without (it falls back to straight lines)
        return
    assert load(name)["synthetic"] is True


def test_meta_and_overview_keys():
    meta = load("meta.json")
    assert {"generated_on", "period_start", "period_end", "assumptions"} <= set(meta)
    assert all({"key", "value", "note"} <= set(a) for a in meta["assumptions"])
    kpis = set(load("overview.json")["kpis"])
    assert {"total_gallons", "avg_net_price_per_gal", "missed_savings_usd"} <= kpis
    assert kpis & {"partner_share_gallons", "tucson_card_gallons_partner_share"}  # renamed in contract v1.7


def test_stations_keys():
    props = {"station_id", "name", "display_name", "city", "brand", "station_class", "is_partner", "partner_discount_cpg", "fills", "gallons",
             "avg_price_paid", "avg_posted_price", "break_even_cpg"}
    fc = load("stations.geojson")
    assert fc["type"] == "FeatureCollection" and fc["features"]
    for f in fc["features"]:
        assert f["geometry"]["type"] == "Point" and props <= set(f["properties"])
    cities = [f["properties"]["city"] for f in fc["features"]]
    assert all(c is None or isinstance(c, str) for c in cities)
    for f in fc["features"]:
        p = f["properties"]
        assert (p["fills"] or 0) >= 0
        if not p["fills"]:
            assert p["avg_price_paid"] is None  # a station with no fills has no price paid


def test_price_history_keys():
    ph = load("price_history.json")
    assert all({"week_start", "price_per_gal"} <= set(e) for e in ph["eia"])
    for rows in ph["stations"].values():
        assert all({"week_start", "avg_price_paid", "avg_posted_price", "gallons"} <= set(r) for r in rows)


def test_routes_keys():
    kinds = {"route": {"route_id", "label", "yard", "base_miles", "base_minutes", "stop_count"}, "stop": {"route_id", "seq", "label"}}
    for f in load("routes.geojson")["features"]:
        p = f["properties"]
        assert kinds[p["kind"]] <= set(p)
        if p["kind"] == "stop":
            assert p["label"] in {"Tucson yard", "Phoenix DC"} or re.fullmatch(r"Customer \d{3}", p["label"])


def test_detours_keys():
    d = load("detours.json")
    assert {"typical_fill_gal", "price_basis", "routes"} <= set(d)
    opt = {"station_id", "insert_after_stop", "extra_miles", "extra_minutes", "price_per_gal", "fuel_cost_usd",
           "detour_cost_usd", "total_cost_usd", "net_savings_vs_baseline_usd"}
    for r in d["routes"].values():
        assert {"baseline_station_id", "best_station_id", "best_reason", "options"} <= set(r)
        assert all(opt <= set(o) for o in r["options"])


def test_missed_keys_and_sorted():
    m = load("missed.json")
    assert {"total_usd", "fill_count", "weekly", "rows"} <= set(m)
    row = {"txn_id", "date", "truck_id", "route_id", "actual_station_id", "alt_station_id", "gallons", "actual_price_per_gal",
           "alt_price_per_gal", "alt_extra_miles", "alt_extra_minutes", "net_savings_usd"}
    assert all(row <= set(r) for r in m["rows"])
    vals = [r["net_savings_usd"] for r in m["rows"]]
    assert vals == sorted(vals, reverse=True)
    assert not (DATA / "missed_paths.geojson").exists()  # replaced by detour_paths.geojson in contract v1.11
    if (DATA / "detour_paths.geojson").exists():
        for f in load("detour_paths.geojson")["features"]:
            assert {"route_id", "station_id", "insert_after_stop"} <= set(f["properties"])
            assert f["geometry"]["type"] == "LineString"


def test_hub_keys():
    h = load("hub.json")
    assert {"sources", "clean_table", "dq", "accuracy"} <= set(h)
    names = [s["name"] for s in h["sources"]]
    assert names[:3] == ["Fuel card export", "GPS telematics export", "Route plan export"]
    assert names[3:] in ([], ["Phoenix DC tank log"])
    for s in h["sources"]:
        assert {"name", "file", "rows", "columns", "sample"} <= set(s) and len(s["sample"]) <= 5
    assert {"name", "rows", "columns", "sample"} <= set(h["clean_table"])
    assert all({"check_name", "rows_in", "rows_out", "issue_count", "notes"} <= set(q) for q in h["dq"])
    assert all({"metric", "value", "notes"} <= set(a) for a in h["accuracy"])


def test_customer_ids_follow_the_contract_format():
    text = " ".join(read(p) for p in DATA.iterdir())
    for found in re.findall(r"CUST-\w*", text):
        assert re.fullmatch(r"CUST-\d{4}", found), found


def test_stations_have_truck_access_and_display_name():
    feats = load("stations.geojson")["features"]
    access = [f["properties"]["truck_access"] for f in feats]
    assert set(access) <= {"confirmed", "unverified"}
    for f in feats:
        p = f["properties"]
        assert p["display_name"]
        if not p["name"] and not p["brand"]:
            assert p["display_name"] == f"Unnamed fuel station · {p['station_id']}"
        elif p["name"]:
            assert p["display_name"] == p["name"]


def test_break_even_follows_spec_formula_bounds():
    """Break-even exists exactly when some route detour to the station is longer than zero, and sits inside the
    range the SPEC section 6 formula allows: fuel and driver time always, plus (at most) the wear term, the
    schedule-risk charge is never part of it (contract v1.11)."""
    cfg = yaml.safe_load(read(REPO / "config" / "assumptions.yaml"))
    mpg, wage = cfg["fleet"]["local_mpg"], cfg["savings"]["driver_cost_per_hour"]
    wear = cfg["savings"]["variable_cost_per_mile"] if cfg["savings"]["include_variable_cost"] else 0.0
    tc = cfg["time_cost"]
    top_mult = max(tc["travel_time_index"], tc["planning_time_index"])
    det = load("detours.json")
    fill = det["typical_fill_gal"]
    for f in load("stations.geojson")["features"]:
        p = f["properties"]
        extras = [o for r in det["routes"].values() for o in r["options"] if o["station_id"] == p["station_id"] and o["extra_miles"] > 0]
        if not extras:
            assert p["break_even_cpg"] is None
            continue

        def cost(miles, mins, price, full=False, mult=1.0):
            eff = mins * mult
            usd = (miles / mpg) * price + (eff / 60) * wage
            if full:  # ceiling: wear on every mile (schedule risk is a ranking penalty only, never a cost)
                usd += miles * wear
            return usd / fill * 100

        lo = cost(min(o["extra_miles"] for o in extras), min(o["extra_minutes"] for o in extras), min(o["price_per_gal"] for o in extras))
        args = (max(o["extra_miles"] for o in extras), max(o["extra_minutes"] for o in extras), max(o["price_per_gal"] for o in extras))
        hi = cost(*args, full=True, mult=top_mult)
        assert p["break_even_cpg"] is not None
        assert lo - 0.1 <= p["break_even_cpg"] <= hi + 0.1, (p["station_id"], p["break_even_cpg"], lo, hi)
        assert "break_even_cpg_peak" not in p  # v1.11: one figure, with break_even_is_peak


def test_detours_toggle_fields_and_baseline():
    det = load("detours.json")
    cfg = yaml.safe_load(read(REPO / "config" / "assumptions.yaml"))
    assert det["include_unverified_default"] == cfg["savings"]["include_unverified_truck_access_default"]
    access = {f["properties"]["station_id"]: f["properties"]["truck_access"] for f in load("stations.geojson")["features"]}
    for r in det["routes"].values():
        assert {"best_station_id_incl_unverified", "best_reason_incl_unverified"} <= set(r)
        assert all(o["truck_access"] == access[o["station_id"]] for o in r["options"])
        ids = {o["station_id"] for o in r["options"]}
        assert r["baseline_station_id"] in ids
        assert access[r["best_station_id"]] == "confirmed"
        assert r["best_station_id_incl_unverified"] in ids
    # the unverified-inclusive pick may or may not differ from the confirmed pick; when it is the same, so is the reason
    for r in det["routes"].values():
        if r["best_station_id"] == r["best_station_id_incl_unverified"]:
            assert r["best_reason"] == r["best_reason_incl_unverified"]


def test_missed_toggle_fields():
    m = load("missed.json")
    assert {"total_usd_incl_unverified", "fill_count_incl_unverified", "weekly_incl_unverified", "rows_incl_unverified"} <= set(m)
    assert all(r["alt_truck_access"] == "confirmed" for r in m["rows"])
    assert all(r["alt_truck_access"] in {"confirmed", "unverified"} for r in m["rows_incl_unverified"])
    # allowing more alternatives can only add missed savings, never remove them
    assert m["total_usd_incl_unverified"] >= m["total_usd"] - 0.02
    assert m["fill_count_incl_unverified"] >= m["fill_count"]
    vals = [r["net_savings_usd"] for r in m["rows_incl_unverified"]]
    assert vals == sorted(vals, reverse=True)
    assert abs(sum(vals) - m["total_usd_incl_unverified"]) < 0.02
    assert abs(sum(r["net_savings_usd"] for r in m["rows"]) - m["total_usd"]) < 0.02
    assert m["fill_count"] == len(m["rows"]) and m["fill_count_incl_unverified"] == len(m["rows_incl_unverified"])
    assert load("overview.json")["kpis"]["missed_savings_usd"] == m["total_usd"]


def test_meta_assumptions_have_shape_and_exist_in_config():
    from fleetfuel import config

    known = {a["key"] for a in config.describe_assumptions()}
    items = load("meta.json")["assumptions"]
    assert items
    for a in items:
        assert {"key", "value", "note"} <= set(a)
    assert {a["key"] for a in items if not a["key"].startswith("derived.")} == known  # every assumption listed, none extra


@pytest.mark.parametrize("name", ["map", "route", "missed"])
def test_toggle_present_and_remembered_safely(name):
    assert "Include unverified truck access" in page(name)
    common = read(SITE / "js" / "common.js")
    assert "localStorage" in common and common.count("try {") >= 2
    assert "Truck access unverified" in common


def test_unverified_dimmed_by_opacity_on_every_map():
    for js in ["index", "map"]:
        assert "'unverified'" in read(SITE / "js" / f"{js}.js") and "opacity" in read(SITE / "js" / f"{js}.js")
    assert ".gm.dim" in read(SITE / "css" / "extra.css")
    assert "Dimmed: truck access unverified" in page("index")
    assert "Dimmed: truck access unverified" in read(SITE / "js" / "map.js")


def test_copy_rules():
    assert "driver time and wear" in read(SITE / "js" / "map.js")
    assert "OpenStreetMap" in page("index") and "synthetic" in page("index")


@pytest.mark.parametrize("name", ["ci.yml", "pages.yml"])
def test_workflows_are_valid_yaml(name):
    doc = yaml.safe_load(read(REPO / ".github" / "workflows" / name))
    assert doc["jobs"]
    text = read(REPO / ".github" / "workflows" / name)
    assert "uv sync" in text
    assert ("uv run pytest" in text) if name == "ci.yml" else ("uv run fleetfuel build" in text)


def test_partner_scout_data_if_present():
    path = DATA / "partner_scout.json"
    if not path.exists():
        return
    d = load("partner_scout.json")
    steps = d["discount_steps_cpg"]
    assert d["synthetic"] is True and steps and d["default_discount_cpg"] in steps
    for s in d["stations"]:
        assert [c["discount_cpg"] for c in s["curve"]] == steps
        if "annual_savings_without_deal_usd" in s:
            assert s["annual_savings_without_deal_usd"] >= 0
            zero = next((c for c in s["curve"] if c["discount_cpg"] == 0), None)
            if zero is not None:
                assert zero["annual_net_savings_usd"] == 0  # the deal creates nothing at a zero discount


def test_route_page_handles_real_data_shapes():
    js = read(SITE / "js" / "route.js")
    assert "allowed(" in js and "with unverified truck access" in js  # unverified options follow the toggle
    assert "Math.round(mn)" in js  # no "5 h 60 min"
    missed = read(SITE / "js" / "missed.js")
    assert "No missed savings in this period." in missed and "renderEmpty" in missed
    assert "Already available at posted prices" in read(SITE / "js" / "assumptions.js")  # v1.11: lives on the How section


def test_scout_callout_does_not_claim_a_typical_fill():
    js = read(SITE / "js" / "index.js").lower()
    assert "typical fill" not in js and "typical_fill" not in js
    assert "actual fills" in js


def test_map_js_has_no_hardcoded_bbox_numbers():
    js = read(SITE / "js" / "map.js")
    assert "tucson_metro_bbox" in js
    assert not re.search(r"-11[0-2]\.\d|\b3[12]\.\d", js)


def test_missed_page_says_latest_week():
    html = page("missed")
    assert "Latest week" in html and "Current week" not in html


def test_map_chip_has_synthetic_price_line_beside_it():
    html = page("map")
    chip = html.index('id="p-chip"')
    assert "benchmark source: U.S. Energy Information Administration" in html[chip : chip + 500]


# ---- v1.17: short value badges and the Start here page ----

@pytest.mark.parametrize("name", PAGES)
def test_strip_links_start_here(name):
    strip = page(name).split('class="strip"')[1].split("</div>")[0]
    assert '<a href="guide.html">New here? Start here</a>' in strip
    assert 'href="assumptions.html"' in strip


def test_guide_item_is_current_only_on_the_guide():
    for name in PAGES:
        sidebar = page(name).split('<aside class="sidebar">')[1].split("</aside>")[0]
        has = 'href="guide.html" aria-current="page"' in sidebar
        assert has == (name == "guide"), name


def test_overview_has_start_here_link_above_scout():
    html = page("index")
    link = html.index('class="start-link"')
    assert 'href="guide.html"' in html[link:link + 200]
    assert link < html.index('id="scout"')


def test_guide_links_every_page_and_assumptions():
    html = page("guide")
    assert "<title>Start here | Fleet Fuel Hub</title>" in html
    assert "Start here: a 5-minute tour" in html
    main = html.split("<main")[1]
    for target in ["index", "map", "route", "missed", "hub", "assumptions"]:
        assert f'href="{target}.html"' in main, target
    assert "fuel on site at the distribution center" in html


def test_derived_from_the_data_badge_is_gone():
    for js in (SITE / "js").glob("*.js"):
        assert "derived from the data'" not in read(js), js.name


def test_badge_limit_and_split_function_exist():
    common = read(SITE / "js" / "common.js")
    assert "BADGE_MAX = 25" in common
    assert "function assumptionParts" in common
    css = read(SITE / "css" / "app.css")
    chip = [ln for ln in css.splitlines() if ln.startswith(".chip {")][0]
    assert "nowrap" not in chip and "max-width: 100%" in chip and "overflow-wrap: anywhere" in chip

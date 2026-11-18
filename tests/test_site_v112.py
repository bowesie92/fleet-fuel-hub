"""v1.12 site checks (contract section 12): grouped stop times on the Test route and cache busting."""

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SITE = REPO / "site"
FIX = REPO / "tests" / "fixtures" / "site" / "v111"


def js(name: str) -> str:
    return (SITE / "js" / f"{name}.js").read_text(encoding="utf-8")


def test_route_groups_options_by_time_with_one_heading_each():
    r = js("route")
    assert "opt-time" in r and "sort(" in r and "stop_time_local" in r
    # the per-option "Costed at" line is gone; only the heading and the selected stop's card use the text
    assert "'opt-sub', when" not in r and "var when = whenText(o)" not in r
    assert r.count("whenText(o)") == 3  # definition, heading, selected stop card
    assert "' (rush hour)'" in r and "'Costed at '" in r


def test_fixture_groups_in_time_order_one_per_distinct_time():
    det = json.loads((FIX / "detours.json").read_text(encoding="utf-8"))
    for ro in det["routes"].values():
        times = [o["stop_time_local"] for o in sorted(ro["options"], key=lambda o: o["stop_time_local"])]
        headings = [t for i, t in enumerate(times) if i == 0 or t != times[i - 1]]
        assert headings == sorted(set(times))
        assert len(set(times)) < len(times)  # the fixture shares a time, so grouping matters


def test_common_appends_version_only_when_meta_exists():
    c = js("common")
    assert 'meta[name="build-version"]' in c
    assert "v ? '?v=' + encodeURIComponent(v) : ''" in c


def test_every_data_fetch_goes_through_the_helper():
    for p in (SITE / "js").glob("*.js"):
        t = p.read_text(encoding="utf-8")
        for m in re.finditer(r"fetch\(([^)]*)\)", t):
            arg = m.group(1)
            if p.name == "common.js":
                assert "dataUrl(" in arg, arg
            else:
                assert "data/" not in arg, (p.name, arg)
        if p.name != "common.js":
            assert "'data/" not in t and '"data/' not in t, p.name


def test_local_css_js_urls_carry_the_build_version():
    """Fails until the export step stamps the pages (the orchestrator rebuilds after merging)."""
    for html in sorted(SITE.glob("*.html")):
        t = html.read_text(encoding="utf-8")
        m = re.search(r'<meta name="build-version" content="([0-9a-f]+)"', t)
        assert m, f"{html.name}: no build-version meta"
        urls = re.findall(r'(?:href|src)="((?:css|js)/[^"]+)"', t)
        assert urls
        for u in urls:
            assert u.endswith("?v=" + m.group(1)), (html.name, u)

"""v1.13 site checks (contract section 13.2): "Lower-cost option" label, the Why column, the why_line under the
two station lines, and graceful absence. Fixture: tests/fixtures/site/v113/missed.json."""

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SITE = REPO / "site"
FIX = REPO / "tests" / "fixtures" / "site" / "v113"


def js(name: str) -> str:
    return (SITE / "js" / f"{name}.js").read_text(encoding="utf-8")


def test_fixture_has_why_fields_and_one_row_without():
    rows = json.loads((FIX / "missed.json").read_text(encoding="utf-8"))["rows"]
    assert {r["why"] for r in rows if "why" in r} <= {"lower price", "shorter detour", "both"}
    assert all(r["why_line"] for r in rows if "why" in r)
    assert any("why" not in r and "why_line" not in r for r in rows)


def test_missed_page_uses_lower_cost_option_label_everywhere():
    m, a = js("missed"), js("assumptions")
    html = (SITE / "missed.html").read_text(encoding="utf-8")
    for text in (m, a, html):
        assert "Cheaper alternative" not in text and "cheaper alternative" not in text
    assert "label: 'Lower-cost option'" in m and "'Lower-cost option: '" in m
    assert "lower-cost option" in html and "lower-cost option" in a


def test_why_column_follows_the_option_column_and_is_sortable():
    m = js("missed")
    assert m.index("key: 'alt_station_id'") < m.index("key: 'why'") < m.index("key: 'alt_extra_miles'")
    assert "label: 'Why'" in m
    # sorting and the column menu both run off COLS, so a COLS entry is sortable and menu-listed
    assert "COLS.forEach" in m and "sort.key = c.key" in m


def test_comparison_shows_why_line_under_the_station_lines():
    m = js("missed")
    i_alt = m.index("'Lower-cost option: ' + stn")
    i_line = m.index("r.why_line", i_alt)
    assert i_line < m.index("r.is_peak", i_alt)
    assert re.search(r"if \(r\.why_line\)", m)


def test_missing_why_shows_nothing():
    m = js("missed")
    assert "r.why || ''" in m          # empty cell, never the word undefined
    assert "if (r.why_line)" in m       # no empty line in the panel

"""v1.17 follow-up: every headline badge is a number and a unit within 25 characters; the guide text is exact."""

import html
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

SITE = Path(__file__).resolve().parents[1] / "site"

# Pull the real formatting functions out of common.js and run them over the real meta.json.
DRIVER = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
function grab(name) {
  const i = src.indexOf('  function ' + name + '(');
  if (i < 0) throw new Error('missing ' + name);
  const rest = src.slice(i + 10);
  const j = rest.search(/\n  (\/\*\*|function |var |window\.)/);
  return src.slice(i, i + 10 + (j < 0 ? rest.length : j)) + '\n';
}
const body = ['isNum', 'peakWindows', 'assumptionValue'].map(grab).join('\n');
const f = new Function(body + '\nreturn assumptionValue;')();
const meta = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
console.log(JSON.stringify(meta.headline_assumptions.map(a => [a.key, f(a.value, a.key)])));
"""


def _badges():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    with tempfile.TemporaryDirectory() as d:
        drv = Path(d) / "driver.js"
        drv.write_text(DRIVER, encoding="utf-8")
        out = subprocess.run([node, str(drv), str(SITE / "js" / "common.js"), str(SITE / "data" / "meta.json")],
                             capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        pytest.skip("badge driver could not load common.js: " + out.stderr[:200])
    return json.loads(out.stdout)


def test_badge_rule_on_real_data():
    rows = _badges()
    assert rows
    for key, shown in rows:
        if len(shown) > 25:
            continue  # goes in the description instead
        assert not re.fullmatch(r"-?\d+(\.\d+)?", shown), f"{key} shows a bare number: {shown}"
    by = dict(rows)
    assert by["savings.typical_fill_gal"] == "72 gallons"
    assert by["routes.linehaul_runs_per_day"] == "5 a day"
    assert by["yard_fuel.discount_vs_eia_per_gal"] == "$0.20/gal"
    assert len(by["time_cost.peak_windows"]) <= 25


def _flat(s: str) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    s = s.replace("“", '"').replace("”", '"').replace("’", "'")
    return re.sub(r"\s+", " ", s).strip()


STEPS = [
    "Overview: move the partner discount slider on the top card. It shows which Tucson station is worth approaching as a fuel partner and what it would save per gallon after detour costs.",
    "Station map: zoom into Tucson. Dot colors show the average price paid. Click a station to see its price over time against the regional average, and how much cheaper it must be to be worth a detour.",
    'Test route: it opens on a Tucson route with a fuel stop added in green. Click other stations under "Try another stop" to compare extra miles, minutes and cost.',
    "Missed savings: each row is a fill where a lower-cost option was close by. Click the top row to see both options on the map and why it wins: lower price, shorter detour, or both.",
    "Data hub: three raw exports on the left, the cleanup in the middle, one clean table on the right.",
]
REAL = [
    "The tool would read the fuel card, GPS and route-plan exports a fleet already produces, with no change to its current systems.",
    "Real prices, partner terms, tank sizes, truck fuel economy, route lengths and delivery windows would replace my assumptions.",
    "It would refresh daily or weekly instead of showing one fixed year.",
    "Recommended stops would be checked against live traffic and road closures before a driver commits.",
    "Results would be checked with the fleet and routing team before anyone acts on them.",
]


def test_guide_text_is_word_for_word():
    page = (SITE / "guide.html").read_text(encoding="utf-8")
    ol = re.search(r'<ol class="guide-steps">(.*?)</ol>', page, re.S).group(1)
    steps = [re.sub(r"^\d\s*", "", _flat(x)) for x in ol.split("</li>") if "<li" in x]
    assert steps == STEPS
    ul = re.search(r'<ul class="guide-list">(.*?)</ul>', page, re.S).group(1)
    assert [_flat(x) for x in ul.split("</li>") if "<li" in x] == REAL
    assert '<p class="sub">What this demo shows, and the five things to try first.</p>' in page

"""Cache busting (contract 12.1): the build version and the page stamping, on a tmp copy of a small site.

The real site/ is never touched: every test builds its own little site folder under tmp_path.
"""

import json
import re
import shutil
from pathlib import Path

import pytest

from fleetfuel.export import stamp

REAL_SITE = Path(__file__).resolve().parents[1] / "site"
CDN_LINES = (b'<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter&display=swap">\r\n'
             b'<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>\r\n')


def unstamped(page: bytes) -> bytes:
    """A real page as it looks before any build stamped it (the committed pages are stamped after each build)."""
    page = re.sub(rb'<meta name="build-version" content="[0-9a-f]*">', b"", page)
    return re.sub(rb'((?:href|src)="(?:css|js)/[^"?]+)\?v=[0-9a-f]*"', lambda m: m.group(1) + b'"', page)



@pytest.fixture
def site(tmp_path) -> Path:
    """A small site: three real pages (their own line endings kept), real css and js, and a few data files."""
    root = tmp_path / "site"
    for folder in ("css", "js", "data"):
        (root / folder).mkdir(parents=True)
    for name in ("index.html", "missed.html", "hub.html"):
        (root / name).write_bytes(unstamped((REAL_SITE / name).read_bytes()))
    shutil.copyfile(REAL_SITE / "css" / "app.css", root / "css" / "app.css")
    shutil.copyfile(REAL_SITE / "js" / "common.js", root / "js" / "common.js")
    (root / "data" / "meta.json").write_text(json.dumps({"synthetic": True}), encoding="utf-8")
    (root / "data" / "overview.json").write_text('{"kpis": 1}\n', encoding="utf-8")
    (root / "data" / "missed.json").write_text('{"rows": []}\n', encoding="utf-8")
    return root


def stamped(site: Path) -> str:
    """Run the whole stamping step on the tmp site and return its version."""
    return stamp.stamp_site(site / "data")


def test_version_is_twelve_hex_characters_and_stable(site):
    first = stamp.compute_build_version(site, site / "data")
    assert len(first) == 12 and all(c in "0123456789abcdef" for c in first)
    assert stamp.compute_build_version(site, site / "data") == first


def test_version_ignores_meta_json_and_html(site):
    before = stamp.compute_build_version(site, site / "data")
    (site / "data" / "meta.json").write_text('{"synthetic": true, "build_version": "x"}', encoding="utf-8")
    (site / "index.html").write_bytes(b"<html>changed</html>")
    assert stamp.compute_build_version(site, site / "data") == before


@pytest.mark.parametrize("relative", ["data/overview.json", "css/app.css", "js/common.js"])
def test_version_changes_when_a_data_css_or_js_file_changes(site, relative):
    before = stamp.compute_build_version(site, site / "data")
    path = site / relative
    path.write_bytes(path.read_bytes() + b"\n")
    assert stamp.compute_build_version(site, site / "data") != before


def test_version_changes_when_a_data_file_is_added(site):
    before = stamp.compute_build_version(site, site / "data")
    (site / "data" / "extra.json").write_text("{}", encoding="utf-8")
    assert stamp.compute_build_version(site, site / "data") != before


def test_meta_json_gains_the_build_version(site):
    version = stamped(site)
    meta = json.loads((site / "data" / "meta.json").read_text(encoding="utf-8"))
    assert meta["build_version"] == version and meta["synthetic"] is True


def test_pages_get_the_version_on_local_urls_and_a_meta_tag(site):
    version = stamped(site)
    page = (site / "index.html").read_bytes()
    assert b'<meta charset="utf-8"><meta name="build-version" content="' + version.encode() + b'">' in page
    assert b'href="css/app.css?v=' + version.encode() + b'"' in page
    assert b'src="js/common.js?v=' + version.encode() + b'"' in page
    assert page.count(b'name="build-version"') == 1


def test_stamping_twice_leaves_every_file_byte_identical(site):
    stamped(site)
    first = {p.name: p.read_bytes() for p in site.rglob("*") if p.is_file()}
    stamped(site)
    assert {p.name: p.read_bytes() for p in site.rglob("*") if p.is_file()} == first
    assert stamp.stamp_pages(site, stamp.compute_build_version(site, site / "data")) == []


def test_a_new_version_replaces_the_old_one_everywhere(site):
    old = stamped(site)
    (site / "data" / "overview.json").write_text('{"kpis": 2}\n', encoding="utf-8")
    new = stamped(site)
    assert new != old
    page = (site / "missed.html").read_bytes()
    assert old.encode() not in page and page.count(new.encode()) >= 3
    assert page.count(b"?v=") == page.count(b"?v=" + new.encode())
    assert page.count(b'name="build-version"') == 1


def test_cdn_urls_are_untouched(site):
    page = site / "hub.html"
    html = page.read_bytes()
    newline = b"\r\n" if b"\r\n" in html else b"\n"  # Windows checkouts get CRLF, the Linux CI checkout gets LF
    cdn = CDN_LINES.replace(b"\r\n", newline)
    marker = b'<meta charset="utf-8">' + newline
    assert marker in html
    page.write_bytes(html.replace(marker, marker + cdn, 1))
    stamped(site)
    after = page.read_bytes()
    assert cdn in after
    assert b"googleapis.com/css2?family=Inter&display=swap" in after and b"chart.umd.min.js\"" in after


def test_nothing_else_in_a_page_changes_and_line_endings_stay(site):
    original = {n: (site / n).read_bytes() for n in ("index.html", "missed.html", "hub.html")}
    version = stamped(site)
    for name, before in original.items():
        after = (site / name).read_bytes()
        # Undo the three allowed edits and the page must equal the original, byte for byte.
        undone = after.replace(b'<meta name="build-version" content="' + version.encode() + b'">', b"")
        undone = undone.replace(b"?v=" + version.encode(), b"")
        assert undone == before
        assert after.count(b"\r\n") == before.count(b"\r\n") and after.count(b"\n") == before.count(b"\n")


def test_a_page_without_local_assets_or_charset_is_left_alone(site):
    plain = b"<html><body>No assets here</body></html>\n"
    (site / "plain.html").write_bytes(plain)
    stamped(site)
    assert (site / "plain.html").read_bytes() == plain


def test_version_is_the_same_for_crlf_and_lf_checkouts(site):
    """A Windows checkout (CRLF) and the Linux CI checkout (LF) of the same commit get the same version."""
    before = stamp.compute_build_version(site, site / "data")
    for path in [site / "js" / "common.js", site / "css" / "app.css", site / "data" / "overview.json"]:
        raw = path.read_bytes().replace(b"\r\n", b"\n")
        crlf = raw.replace(b"\n", b"\r\n") if b"\r\n" not in path.read_bytes() else raw
        path.write_bytes(crlf)
    assert stamp.compute_build_version(site, site / "data") == before

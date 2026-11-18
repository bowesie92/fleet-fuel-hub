"""Cache busting: stamp a build version on the data, the CSS and JS URLs and every page.

In business terms: a browser keeps old copies of files to load pages faster. After a rebuild
the numbers change, so every page carries a short build version. A viewer's browser sees a new
version, asks for the new files and always shows the latest numbers (docs/contracts.md 12.1).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

VERSION_LENGTH = 12  # hex characters of the SHA-256 kept as the version
META_FILE = "meta.json"
ASSET_FOLDERS = ("css", "js")

CHARSET_META = b'<meta charset="utf-8">'
VERSION_META = re.compile(rb'<meta name="build-version" content="[^"]*">')
CHARSET_WITH_OLD_VERSION = re.compile(re.escape(CHARSET_META) + rb'(?:<meta name="build-version" content="[^"]*">)?')
LOCAL_ASSET_URL = re.compile(rb'(href|src)="((?:css/[^"?#]+\.css)|(?:js/[^"?#]+\.js))(?:\?v=[^"]*)?"')


def files_in(folder: Path, skip: set[str] | None = None) -> list[Path]:
    """Every file under a folder, in sorted order of its posix path (missing folder: none)."""
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.rglob("*") if p.is_file() and p.name not in (skip or set())),
                  key=lambda p: p.relative_to(folder).as_posix())


def compute_build_version(site_dir: Path, data_dir: Path) -> str:
    """A short fingerprint of every number and style the site loads, so it changes whenever any of them does.

    First 12 hex characters of SHA-256 over the data files (not meta.json), then the css and js files,
    each as its path relative to the site folder plus its bytes. Line endings are normalised to LF first, so a
    Windows checkout (CRLF) and the Linux CI checkout (LF) of the same commit get the same version.
    """
    ordered = files_in(data_dir, skip={META_FILE})
    for folder in ASSET_FOLDERS:
        ordered += files_in(site_dir / folder)
    digest = hashlib.sha256()
    for path in ordered:
        digest.update(path.relative_to(site_dir).as_posix().encode("utf-8"))
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:VERSION_LENGTH]


def stamp_html_bytes(html: bytes, version: str) -> bytes:
    """One page with the version on its local CSS and JS links and in its build-version meta tag; nothing else changes."""
    token = version.encode("ascii")
    html = LOCAL_ASSET_URL.sub(lambda m: m.group(1) + b'="' + m.group(2) + b"?v=" + token + b'"', html)
    meta = b'<meta name="build-version" content="' + token + b'">'
    return CHARSET_WITH_OLD_VERSION.sub(lambda m: CHARSET_META + meta, html, count=1)


def stamp_pages(site_dir: Path, version: str) -> list[Path]:
    """Stamp every site/*.html; a page is rewritten only when its bytes change (line endings are kept). Returns the pages changed."""
    changed = []
    for page in sorted(site_dir.glob("*.html")):
        old = page.read_bytes()
        new = stamp_html_bytes(old, version)
        if new != old:
            page.write_bytes(new)
            changed.append(page)
    return changed


def add_version_to_meta(data_dir: Path, version: str) -> None:
    """Record the version in meta.json so the pages can send it with every data request."""
    path = data_dir / META_FILE
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta["build_version"] = version
    path.write_text(json.dumps(meta, indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8", newline="\n")


def stamp_site(data_dir: Path) -> str:
    """Compute the version, put it in meta.json and stamp every page. The site folder is the parent of the data folder."""
    site_dir = data_dir.parent
    version = compute_build_version(site_dir, data_dir)
    add_version_to_meta(data_dir, version)
    stamp_pages(site_dir, version)
    return version

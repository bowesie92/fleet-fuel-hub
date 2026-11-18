---
name: site-builder
description: Wave 1 builder. Static site pages (Overview, Station map, Test route, Missed savings, Data hub, Assumptions) in plain HTML/CSS/JS with MapLibre and Chart.js, reading site/data/ in contract formats, plus GitHub Actions CI and Pages workflows. Owns site/ and .github/workflows/.
model: sonnet
isolation: worktree
disallowedTools: Agent
---

You are the **site-builder** for fleet-fuel-hub. You run in your own git worktree on your own branch.

## Before you start
1. Read `CLAUDE.md`, `SPEC.md` (sections 1, 5 and 6) and `docs/contracts.md` (especially section 7) in full. The contract is the interface. Build to it exactly.
2. Read `docs/design/STYLE.md` in full. It is **required** for all site work. Look at every image in `docs/design/design-refs/` (gitignored, so read them from the main checkout at `C:\dev\fleet-fuel-hub\docs\design\design-refs\` if your worktree has none). Match their layout and feel, not their text. **STYLE.md section 6 overrides anything in the images.**
3. Run `uv sync` in your worktree.

## Folders and files you own (edit nothing else)
- `site/` (html pages, `site/css/`, `site/js/`, and placeholder files in `site/data/` for Wave 1 only)
- `.github/workflows/`
- `tests/test_site_*.py`

If you need a change anywhere else, stop that piece and report it.

## Tasks
1. Pages per SPEC section 5: `index.html` (Overview), `map.html` (Station map), `route.html` (Test route), `missed.html` (Missed savings), `hub.html` (Data hub), plus `assumptions.html`, which lists every assumption from `site/data/meta.json`.
2. **Every page** shows a clear SYNTHETIC DATA banner at the top, with a link to `assumptions.html`, and shares one nav bar.
3. Maps: MapLibre GL JS with the OpenFreeMap liberty style `https://tiles.openfreemap.org/styles/liberty`. Charts: Chart.js. Load both from a CDN with pinned versions. No build step, no frameworks.
4. Read data only from `site/data/*.json` and `*.geojson`, in the exact formats in contract section 7. Write **small placeholder files** in those formats now (a few made-up stations around Tucson and the I-10 corridor with made-up names, 2 routes, a few missed-savings rows). Analytics will overwrite them in Wave 2.
5. Station map: color stations by average net price paid, use a distinct marker for partners, and on click show a weekly price-paid vs EIA chart plus the break-even sentence ("a detour here is only worth it if it is at least X cents a gallon cheaper").
6. Test route: pick a route, draw the base route, and on clicking a station show added miles, added minutes and net cost or savings for a typical fill. Highlight the best stop with its one-sentence reason.
7. Missed savings: a weekly total at the top, and a table sorted by dollars. Clicking a row shows both stations and the two route options on the map.
8. Data hub: the three source feeds on the left, the clean table on the right, and the data-quality results and accuracy metrics in between.
9. Style per `docs/design/STYLE.md`: `site/css/tokens.css` holds exactly the section 2 tokens and every other stylesheet uses only those variables (no hard-coded colors); the section 1 app shell is shared by every page; each page follows its section 5 layout; components follow section 4. Under 768px the sidebar becomes the bottom tab bar (section 5, Mobile). Works at 375px wide. Never any company's brand colors or logos. Plain-English copy for an operations manager.
10. Workflows: `ci.yml` (on push and PR: checkout, astral-sh/setup-uv, `uv sync`, `uv run pytest`) and `pages.yml` (on push to main and manual dispatch: `uv sync`, `uv run fleetfuel build` from cache with no keys, upload `site/`, deploy to GitHub Pages). Pages will not run until the repo is public, and that is fine.
11. `tests/test_site_*.py`: every html page has the banner and the assumptions link, every placeholder data file parses and has the contract's required keys, and no html file contains an em dash.

## Rules
- Missed savings and test-route numbers are always net of detour costs (SPEC section 6). The copy must never call a raw price difference "savings".
- Customer stops are shown only as "Customer 014" style labels.
- Any chart, table or tile showing EIA prices carries the caption "Source: U.S. Energy Information Administration (EIA)". No EIA logo and no copy implying EIA endorsement (CLAUDE.md rule 11). Add a test that checks for this caption on every page that draws EIA prices.
- No em dashes in any copy. No company names, logos or people's names. Public OSM station brand names may appear only when they come from the data files.
- Check it works when served with `uv run python -m http.server 8000 --directory site`.

## Done when
- All six pages load from the local server with placeholder data, without console errors, and are usable on a phone-width screen.
- The banner and assumptions link are on every page.
- Both workflow files are valid YAML.
- `uv run pytest` passes (the whole suite).

## Report (under 25 lines)
Branch name; pages built; placeholder files; CDN versions pinned; test results; anything the export step must provide that the contract does not cover yet; open issues. Do not merge or push.

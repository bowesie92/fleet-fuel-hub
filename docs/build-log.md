# Build log

One short entry per milestone or agent wave: what was built, key decisions, and what to understand before moving on.

## 2026-11-16: Phase A, setup (orchestrator only)

- **Built:** private GitHub repo, safety files (`.gitignore`, `.env.example`), permissions in `.claude/settings.json`, seven agent definitions in `.claude/agents/`, the uv package scaffold with stub stages, `config/assumptions.yaml`, `docs/contracts.md`, and `scripts/check_repo_rules.py`.
- **Decision: contracts before code.** Four agents will build in parallel without seeing each other's work, so `docs/contracts.md` pins every file's columns, the DuckDB DDL and the site JSON shapes. Each agent tests against its own fixtures in that exact format.
- **Decision: fixed entry points.** `cli.py` calls a `run()` function in each subpackage, so no two agents ever edit the same file. That removes the most likely merge conflict.
- **Decision: added a posted price feed** (`station_prices.csv`, `ref_station_price`). Missed savings needs the price at stations a truck did *not* use, and a fuel card export only shows prices where it did fuel.
- **Decision: price offsets by station class** (travel center, branded, unbranded) instead of by real brand, so the repo makes no pricing claims about any real company.
- **Decision: customer cache is sanitized before it is written** (coordinates only). This is the one exception to "cache the raw response", because anonymity outranks it.
- **Understand before moving on:** the difference between a *source* file (messy, vendor-shaped, in `data/raw/`), the *hub* (`data/hub.duckdb`, clean, one schema), and *ground truth* (what the generator really did, used only to score the cleanup). That three-layer split is the heart of the demo.
- **Repo rules check:** `uv run python scripts/check_repo_rules.py` scans for forbidden terms (counts and paths only) and em dashes. It also runs inside `uv run pytest`.

## 2026-11-16/17: Wave 1, data, generator, hub and site (four builders, overnight)

- **Built:** real EIA diesel prices (210 weeks, series found through the API catalog) and OSM truck stations, all cached; a seeded generator for a year of fleet operations with three messy exports and an answer key; the ingest and cleaning pipeline into `data/hub.duckdb`; and six site pages in the STYLE.md look, still on placeholder data.
- **Result on real inputs:** station matching precision 1.000 and recall 0.949 (16 of 313 swipes left unmatched rather than guessed, mostly bare "FUEL STOP" text), duplicate removal 5 of 5, odometer repair 18 of 18. qa-reviewer recomputed all of these from the raw tables and passed the wave after one fix round (two must-fix items: a code fallback in synth and a missing site field). Every cleanup step writes a plain-English `dq_log` row for the Data hub page.
- **Decision: truck access is explicit.** Every station is `confirmed` or `unverified`. Recommendations and missed savings use confirmed stations unless the viewer turns on a toggle, so the demo never recommends a station a tractor may not fit.
- **Decision: manual station fixes live in one file** (`config/station_overrides.yaml`), keyed by the OSM reference, and each one is logged. That is an audit trail: every human change to the data is visible and explained.
- **Decision: the contract and its code land together.** `main` stays green, and the integration branch carries changes until the tests pass.
- **Ownership note:** the orchestrator regenerated `site/data/meta.json` once (commit 8c59a88), outside its folders, because new config keys broke a site test. From Wave 2, export owns that file.
- **Blocker:** only 2 confirmed truck stations in the Tucson area (decision D0), so Wave 2 is on hold for Cass.
- **Understand before moving on (Wave 1):** precision vs recall. Precision 1.000 means no swipe was matched to the wrong station. Recall 0.949 means about 5% were left for review because the evidence was thin. In audit terms, the matcher prefers an open exception to a wrong posting.

## 2026-11-17: Wave 2, real routing, the savings math, the partner scout (six agents)

- **Built:** truck routes and route-by-station detours from openrouteservice (all cached; a second build makes zero calls); the SPEC section 6 net-savings math, missed savings and break-even; the Tucson partnership scout as the Overview lead insight; and export of every site file, so the site now runs on real exported data.
- **Station set (Cass's D0 answer):** four Tucson truck sites confirmed only from the chains' own locators (one Pilot, three CFN cardlocks), each with a source URL in `config/station_overrides.yaml`. With the Pilot Express, Tucson has 5 confirmed stations and 13 unverified diesel stations. Six borderline sites went to Cass for a Street View check instead of being confirmed.
- **Sanity check fixed a real inconsistency:** six linehaul tractors but one run a day. Now five runs a day, so fills went from 313 to 682 a year.
- **Result:** 80,549 gallons, average net price $4.945, missed savings $2,832.11 over 104 fills (three hand-checked to the cent), partner share 68%. At 12 cents off, the best Tucson partner prospect is a CFN cardlock, worth about $2,886 a year in savings the deal itself creates.
- **Decision: the scout counts only what the deal creates.** Savings available at posted prices are missed savings and are shown separately, so no dollar is counted twice.
- **Finding:** the truck access toggle changes no recommendation on this data, because the cardlocks (priced 10 cents below the EIA average) always beat the unverified retail stations.
- **Understand before moving on:** the difference between a price gap and a net saving. A 21 cent cheaper station saved only $5.07 on a 72 gallon fill once 5.7 extra miles and 11 minutes of driver time were counted.

## 2026-11-17: Pre-review fixes (contract v1.6)

- **Phoenix DC yard fueling:** linehaul tractors now fill at an on-site tank before departure, priced at EIA weekly minus an assumed 30 cent bulk discount. Result: 1,508 yard fills (64,657 gal) and 223 card swipes, all Tucson local. A linehaul round trip uses about 20% of the tank, so no linehaul truck ever needs the card (decision Y1). A missing tank log now stops the build instead of reading as zero.
- **One cost formula:** break-even now calls the same cost function as net savings, wear included, using one median Tucson route per station. At the break-even gap, a typical fill costs the same at both stations.
- **New headline numbers:** 80,697 gallons (16,040 card, 64,657 yard), card average $5.047/gal, missed savings $519.63 over 58 fills ($25.98 per truck per year), partner share of card gallons 0% (decision Y2). Best Tucson partner prospect at 12 cents: CFN Benson Hwy, about $1,554 a year ($77.70 per truck per year).
- **Transparency:** every page that shows a price carries the EIA source line, and Overview and Missed savings show the "Assumptions behind these numbers" panel with units.
- **CFN sources:** cfnnet.com, CFN's own site, links to the cfnfleetwide.com locator; each entry cites that page too.
- **Understand before moving on:** the yard tank changed the story. When most gallons come from a cheap bulk tank, card spending, and so missed savings, is small and all in Tucson. The partner scout's Tucson deal becomes the main lever.

## 2026-11-17: Review changes (contract v1.7)

- **Headlines are rates:** missed savings lead with 3.24 cents per card gallon, and the Tucson partner scout with 12.50 cents per captured gallon at a 12 cent discount (CFN Benson Hwy). Dollar totals and per-truck figures are secondary lines.
- **Stated partner set:** the partner program is the six truck-confirmed stops on I-10 between Casa Grande and Eloy, a written assumption instead of a random draw. No Tucson route passes them, so "Tucson card gallons at partner pricing" is 0%, which the tile links to the scout.
- **Two EIA captions:** the plain EIA source line appears only on the two charts that plot real EIA numbers; every synthetic price says it is built from the EIA weekly diesel average with EIA as the benchmark source. This is now CLAUDE.md rule 11 and qa-reviewer check 12.
- **Understand before moving on:** a rate travels and a total does not. 3.24 cents a gallon can be compared across fleets of any size; $519.63 cannot. And 12.50 cents is slightly above the 12 cent discount because moved fills keep small price gaps that already existed (decision R1).

## 2026-11-17: Time-cost upgrade (contract v1.9)

- **Built:** detour time now reflects Tucson rush hour. In the FHWA weekday peak windows (6 to 9 am, 4 to 7 pm), detour minutes are scaled by Tucson's Travel Time Index (1.07, averaged from FHWA's Urban Congestion Report, Oct 2025 to Sep 2026). There is an optional Planning Time Index buffer (1.38), plus guardrails: a 45-minute detour cap, a schedule-risk charge for stops before the last delivery, and overtime past a 10-hour shift.
- **Licensing checked:** TomTom's free tier bans caching and derived data, so it was not used. openrouteservice's terms publish results under CC BY-SA 4.0, and OpenStreetMap data is ODbL. Both are recorded in `DATA_LICENSES.md` and credited on the site.
- **Result:** missed savings rose to $618.93 over 62 fills (3.86 cents per card gallon), mostly from schedule risk. The Test route worked example: a 30-minute detour at 5 pm costs $33.85, so the station must be 37.6 cents a gallon cheaper to pay off.
- **Understand before moving on:** time is often the biggest part of a detour's cost. At $38 an hour, half an hour of driving costs more than the fuel burned, which is why a station needs a large price gap before a detour pays.

## 2026-11-17: Final assumption pass (contract v1.10)

- **Sources on every cost:** each cost assumption names its source with year and URL, shown as links in the Assumptions panel:
  - BLS OEWS for the loaded driver cost;
  - NACFE Run on Less Regional for local MPG;
  - ATRI 2026 for linehaul MPG and wear, marked "as reported, not checked by this project" because the report is registration-only;
  - eCFR 49 CFR 395 for hours of service.
- **Overtime replaced:** federal overtime is weekly and many drivers are exempt under FLSA 13(b)(1), so the overtime term is gone. An hours-of-service check takes its place. The longest route day, with a full detour, is 5.8 hours of driving and 7.4 hours on duty, against limits of 11 and 14, so all routes are within federal limits.
- **Yard discount:** the Phoenix yard discount is 20 cents (average yard price $5.006). Headline card numbers are unchanged: 3.86 cents per card gallon missed.
- **Understand before moving on:** an assumption is only as good as its source. Where the source could not be checked, the page says so instead of presenting the number as fact.

## 2026-11-17: Site review fixes (contract v1.11)

- **Cost rules:** schedule risk is now a ranking penalty only. It never enters a dollar figure (missed savings, break-even, the scout, the Test route). The Tucson Travel Time Index is 1.17 (Texas A&M 2025, freeways and arterials), with FHWA's 1.07 shown as the cross-check. The detour cap is 15 minutes and the typical fill is 72 gallons (35% to 95% of a 120-gallon tank).
- **Top missed row rechecked (TUC-08, Jul 24, Pilot vs CFN 1792):**
  - The figure is $33.60 = price gap $13.27 (19.8 cents x 67.0 gal) + $20.88 for the 8.4-mile, 15-minute rush-hour detour the driver made to Pilot - $0.55 to reach CFN 1792, which is on the route.
  - The old $40.71 included $8.03 of schedule risk, and the page wrongly said the station used was "0 extra miles". qa-reviewer recomputed it from the hub, and the three hand-checked fills (13.25, 9.24, 12.23) by hand.
- **Test route:**
  - It opens on Route R04, the biggest saving from adding a station ($10.24).
  - Each option shows one cost, for the planned weekday time its stop happens.
  - Detours are drawn on real roads: 337 cached openrouteservice legs (197 live calls once, 0 on a rebuild).
  - After merge the orchestrator tightened the best-stop rule in analytics' file (`detour_options.choose_best`): only a stop that saves real dollars can win. Without it, R01 recommended a stop costing $3.44 more.
- **Copy and credit:**
  - The "Per extra mile" column is gone.
  - The scout reads "saved per gallon moved to this station", and its method sits behind "How this is calculated".
  - Every page carries "Built by Cass Bowes" with a GitHub link.
  - Code is MIT licensed.
- **New headline numbers:** missed savings $550.06 over 61 fills (3.43 cents per card gallon); top partner prospect CFN 1792 Benson Hwy at 11.7 cents saved per gallon moved, about $1,461 a year.
- **Understand before moving on:** a missed saving compares two whole trips, not two pump prices. When the driver detoured to the pricier station and the cheaper one sat on the route, the avoided detour is a real part of the saving, so it can be larger than the price gap times gallons. Showing the parts makes that checkable.

## 2026-11-17: Last small fixes (contract v1.12)

- **Final hand checks:** `docs/hand-checks.md` holds three real fills with full arithmetic: FC1774473 $33.60, FC0733626 $9.39 and FC9591385 $6.03. `tests/test_analytics_handchecked.py` checks them against the raw files, the shared cost function and `missed.json`. The earlier report figures are explained in a history table there: $32.03/$8.06/$5.08 (v1.6, before rush hour) and $40.71/$8.94/$5.68 (v1.9, FHWA 1.07 plus schedule risk). $13.25/$9.24/$12.23 were made-up fixture fills, now in `tests/test_analytics_fixture_fills.py`.
- **Test route:** options are grouped under one "Costed at 7:31 am" heading per stop time (Cass's choice), since one route's stops happen at different times.
- **Cache busting:** the build computes a version from the data, CSS and JS (SHA-256, 12 characters), writes it to `meta.json`, and stamps `?v=` on every CSS and JS URL in the pages. `common.js` adds the same `?v=` to every data request. The same files always give the same version, so a rebuild with no changes leaves the pages untouched.
- **Orchestrator fix after merge:** `tests/test_export_stamp.py` copied the real pages as input, which broke once the real pages were stamped. Its fixture now strips any stamp first.
- **Understand before moving on:** browsers keep old copies of files. Changing the URL whenever the content changes is the standard way to force a fresh download without turning caching off.
- **QA follow-ups (orchestrator, after qa-reviewer passed v1.12):**
  - The hand-check test now also checks gallons, prices, stations, route and insertion points against the raw files and `missed.json`, plus the typed detour inputs against `ref_detour` (skipped when no hub is built).
  - The breakdown on Missed savings and Assumptions carries a one-line rounding note.
  - Left as noted, not changed: stamping a page whose charset tag differs gives no version meta; `meta.json` is not in the hash; options with no stop time join the last group.

## 2026-11-17: Why the lower-cost option wins, Pages readiness (contract v1.13)

- **Missed savings:**
  - "Cheaper alternative" is now "Lower-cost option".
  - A Why column says "lower price", "shorter detour" or "both".
  - The comparison panel says it in one line, for example "Higher pump price, but saves a 7.7-mile detour."
  - Of the 61 rows: 26 win on both, 17 on price alone and 18 on a shorter detour alone, at a higher pump price.
- **S1 closed:** borderline Tucson sites stay unverified; five of the six are not in OpenStreetMap at all.
- **Pages prepared, not enabled:** a fresh clone with no keys builds, tests pass and the tree stays clean. A scan of every file version in history found no forbidden terms or secrets (details under D14).
- **Two portability fixes found on the way:**
  - A stamp test assumed Windows line endings and failed in CI on Linux.
  - The build version hashed raw bytes, so Windows and Linux checkouts disagreed. It now normalises line endings.
- **Understand before moving on:** "lower cost" is not "lower price". About 3 in 10 missed savings come from fueling at a slightly pricier pump that is on the route, instead of driving miles out of the way.
- **QA follow-ups (orchestrator, after qa-reviewer passed v1.13):**
  - The Why line's miles now match the two mile figures printed above it. It copies the page's own rounding, and all 86 lines with a mile figure were checked in the browser.
  - "Lower pump price and a shorter detour" replaces "...and saves a shorter detour".
  - The page copy says "lower-cost" instead of "cheaper".
  - The Pages deploy job is skipped while the repo is private and runs by itself once it is public.
  - Left as noted: `generated_on` is the build date, so a build on a later day changes `meta.json`.

## 2026-11-17: Customer points moved off real businesses (contract v1.14)

- **Why:** before going public, no stand-in customer stop may sit on a real business.
- **Customer points:** every point moved a seeded 300 to 600 m (301 to 596 m in practice), never within 50 m of any original point. CUST IDs are unchanged. The OSM cache now keeps only the moved points, and the 695 real points that were never used are dropped.
- **Rebuild:**
  - the fleet data was resynthesized, and every Tucson routing call refetched;
  - 236 cache files that held old coordinates were pruned;
  - all 337 detour legs are drawn on roads, with 0 straight-line fallbacks.
- **Rate limit:** the first rebuild hit openrouteservice's "429 Rate Limit Exceeded", because export made a new client per leg and so never paused.
  - One shared limiter now keeps every client to 30 calls a minute.
  - A 429 backs off (Retry-After if sent, else 65 s then 130 s), with at most 3 tries.
  - The refresh is resumable and prunes only after a run with no failures.
  - Calls today: 376 directions and 10 matrix succeeded (limits 2,000 and 500), plus 125 refused with 429.
- **Rules:** hard rule 12, never read `.env` or `.local` by any means. Two committed exceptions: the repo checker (counts only) and the app's config loader. `scripts/check_repo_rules.py` gained `--history`, `--secrets` and `--repo`. The new `scripts/check_customer_offsets.py` proves the move. Both were tested against planted samples.
- **New headline numbers:** missed savings $408.61 over 42 fills (2.60 cents per card gallon). The final hand checks are the new fills FC2028603, FC4274959 and FC0528809 (one per "Why" answer).
- **Branches:** the two merged `claude/` branches are deleted on GitHub. A publishing plan is in `docs/publishing-plan.md`, not run.
- **Understand before moving on:** anonymizing a location means moving it, not just renaming it. A label like "Customer 014" hides the name, but the coordinates alone could still point to the business.

## 2026-11-17: The customer move made irreversible (contract v1.15, after a qa-reviewer FAIL)

- **What QA found:** the v1.14 move used the public seed for both the sample and the offsets. Replaying that stream put every moved point back within 5 m of its original, and the seeded sample alone named each business by CUST order.
- **Fix:**
  - The sample, order and offsets are drawn once from unseeded randomness (`secrets.SystemRandom`) and stored nowhere.
  - The cache of moved points is the only source of truth.
  - CUST IDs are still CUST-0001 to CUST-0300, but which business each started from is now unknown even to the repo.
- **Reachability (found live):** the first unseeded draw put a stop inside an air base, where trucks cannot route. The draw now asks openrouteservice's snap service (all 300 points in one call) and redraws only the unreachable points. It needed one redraw round.
- **Checks:**
  - `check_customer_offsets.py` now checks all 995 old points and parses JSON properly. Every point is 300 to 600 m from some original and at least 52 m from all of them.
  - `check_repo_rules.py` fails a history scan when the terms list is missing.
  - Rule 12 names the station-name screening as a third reader.
  - `scripts/public_push_guard.py` blocks pushing private history to the public repo.
  - The publishing plan now starts with repointing every other clone.
- **Rebuild:** 338 detour legs, 0 straight-line fallbacks, 195 stale files pruned. Missed savings $404.25 over 38 fills (2.51 cents per card gallon). Final hand checks: FC2438413, FC8300755, FC2072415.
- **Routing:**
  - A Retry-After over 120 s fails instead of sleeping.
  - Every retry counts toward the daily limit, and refused retries are cached.
  - Calls today: 547 directions, 21 matrix and 3 snap succeeded (limits 2,000, 500 and an assumed 500), plus 125 directions refused with 429 earlier.
- **Understand before moving on:** a random move is only private if the randomness is secret. Seeding makes data reproducible, which is right for synthetic data and wrong for hiding real locations.

## v1.16: at least 3 candidate businesses per customer point (Nov 18)
- **Built:** a new rule `fetch.customers.min_candidate_originals: 3`. A moved point is kept only if at least 3 original businesses lie 300 to 600 m from it. 38 points failed (16 had one candidate, 22 had two). `scripts/redraw_thin_customers.py` redrew only those, unseeded, under every other rule, and re-checked truck reachability (3 snap calls; some redraws needed a second try). The other 262 points did not move. Where no current candidate left room, the fallback drew the point around another random original, so a few stops changed neighbourhood (one moved about 23 km).
- **Checks:** `check_customer_offsets.py` now fails on any point below 3 candidates and counts each original place once. All 300 now have 3 or more; the closest any point gets to an original is 52 m; 0 coordinates within 5 m of an original or of the 38 replaced positions. One synthetic GPS ping logged at 21 mph passes 3.6 m from a business on its road; moving pings are now reported separately instead of failing.
- **Rebuild:** resynth, 338 detour legs, 0 failed, 0 straight-line fallbacks, 157 stale cache files pruned. openrouteservice today: 136 new directions answers (about one call each), 10 matrix and 3 snap calls, well under the 2,000 and 500 daily limits. Missed savings now $401.32 over 24 fills (212 fills analysed).
- **Hand checks:** FC5659816 ($46.94, both), FC8960414 ($8.74, lower price), FC4426227 ($6.72, shorter detour).
- **Understand before moving on:** offsetting a point is not enough on its own. It also needs a crowd: if only one real business could be the source, the offset hides nothing. This is the same idea as k-anonymity, here with k = 3.

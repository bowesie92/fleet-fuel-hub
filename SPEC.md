# Fleet Fuel Hub: Project Spec

Working name: **fleet-fuel-hub**
Owner: Cass Bowes
Status: v1 spec, Nov 16 2026
Target: v1 live by Tue Dec 1, 2026

---

## 1. Why this exists

Regional food distributors commonly run into three problems:

1. Their tools don't talk to each other. Fuel, routes and truck data sit in separate apps.
2. Drivers sometimes fuel at a pricier station when a cheaper (often partner) station was an easy detour.
3. They want to know which stations are worth adding as fuel partners.

**Demo scenario (hypothetical):** one distribution center in Phoenix and a satellite yard in Tucson. A Phoenix truck drops a trailer at the Tucson yard and a Tucson driver delivers it. The Phoenix DC has an on-site bulk diesel tank: linehaul tractors fill there before each departure, priced at the EIA weekly average minus an assumed bulk discount (`yard_fuel.*` in config). Those yard fills are not fuel card transactions and never count as missed savings; a linehaul card fill happens only as an exception (low fuel mid-trip). The Tucson yard is assumed to have no on-site fueling, so Tucson fills happen at public stations with the fuel card.

The demo shows:

- A map of stations with the average price paid at each one over time.
- A test route with a station added to it.
- A case where a driver used a pricier station when a cheaper one was easy to reach.

**The demo answers those three asks, built on one unified data layer, which is the real pitch.**

### Rules that apply to everything

- **Public and synthetic data only.** Never their real data. The bring-your-own-data mode (Phase 3) runs entirely in their browser, and nothing is uploaded.
- **Not branded as the company.** No company name, logo or colors in the public repo or site. The README describes a generic, hypothetical distributor. Nothing in the repo refers to a real company.
- **Every screen says SYNTHETIC DATA.** Every assumption is visible and editable in one config file.
- **Net savings, never raw price difference.** See section 6.
- **Customer stops are anonymized.** Real commercial coordinates are fine, but stops are labeled "Customer 014", never with a real restaurant's name.

---

## 2. Architecture at a glance

```
synth/  (seeded generator, ground truth)
   |
   v  writes three deliberately messy source files
data/raw/fuel_card.csv     (like a fuel card export)
data/raw/telematics.csv    (like a GPS/ELD ping export)
data/raw/route_plans.csv   (like a routing software export)
data/raw/eia_diesel.csv    (REAL: EIA weekly diesel, West Coast less California)
data/raw/stations.geojson  (REAL: OpenStreetMap truck-capable fuel stations)
   |
   v  ingest/  one adapter per source, validates columns and types
   v  normalize/  station matching, dedupe, odometer repair
data/hub.duckdb  (ONE database: the "single app")
   |
   v  analytics/  price history, net savings, missed savings, partner scout, exceptions
   v  export/  small JSON and GeoJSON files
site/  (static page: MapLibre map, charts, tables)
   |
   v  GitHub Actions builds it and publishes to GitHub Pages
```

### Stack and why

| Piece | Choice | Why |
|---|---|---|
| Language | Python 3.12 | What you already know from CIS 507. |
| Env and deps | uv, with uv.lock committed | Same environment on any machine with one command. |
| Database | DuckDB (single file) | SQL, no server, fast, and it *is* the "one place" story. |
| Data frames | pandas | Familiar from CIS 507. |
| Tests | pytest | Standard. |
| Routing | openrouteservice hosted API, `driving-hgv` (truck) profile | Free key, truck-aware routing, no Docker needed. |
| Map tiles | OpenFreeMap + MapLibre GL JS | Free, no API key, no account. |
| Charts | Chart.js | Simple, small. |
| Hosting | GitHub Pages via GitHub Actions | Free, never goes down because your PC is off. |

### Why static, not a live app

A live server (Streamlit, n8n, the Beelink) can be asleep or off when a viewer clicks. A static site can't be. All heavy work happens at build time. The "test route" interaction works by **precomputing every route x station detour** (about 10 routes x 40 stations = 400 combos), so clicking a station is just a lookup.

---

## 3. Data sources

### Real data

| Source | What | How |
|---|---|---|
| EIA weekly retail diesel | "West Coast (PADD 5) except California" series. This region includes Arizona. | EIA Open Data API v2 (free key). Save to `data/raw/eia_diesel.csv`. |
| OpenStreetMap stations | `amenity=fuel` within a bounding box covering the Phoenix to Tucson I-10 corridor and Tucson metro. | Overpass API query. Keep stations tagged `hgv=yes` or `fuel:HGV_diesel=yes`, **plus** stations whose `brand` is a truck-stop brand (Love's, Pilot, Flying J, TA, Petro), because OSM truck tags are incomplete. **Tucson metro only** (a box covering the I-10 corridor from Marana to Vail and the I-19 junction), also keep (a) cardlock and commercial fleet fueling sites (fleet-network brand, name or operator, or `amenity=fuel` with `access=customers` or `private` and `fuel:diesel=yes`) and (b) any `fuel:diesel=yes` station with hgv or truck-access evidence. OSM tagging in Tucson turned out too thin for these rules (Nov 16 finding), so two more sources apply in the Tucson box: (c) **chain-locator confirmations**, added by hand as `add` entries in `config/station_overrides.yaml`, each with name, coordinates, source URL and reason, and only from authoritative public evidence (the chain's own location finder showing diesel or truck lanes, or a fleet cardlock network's own locator; never satellite imagery); and (d) every other `fuel:diesel=yes` station, kept as `unverified`. Every station gets `truck_access` = `confirmed` or `unverified`. Thin options are a finding to report, not a blocker. Unverified stations show dimmed on the map and are left out of recommendations and missed-savings alternatives unless the site toggle "Include unverified truck access" is on. Manual fixes (exclude, merge, rename) live only in `config/station_overrides.yaml` and each one is logged in `dq_log`. A station whose name matches a forbidden term is excluded with reason "name conflict" and kept out of every committed cache file. Hand-check the final list on the map. |
| Road routing | Truck route geometry, distance and time. | openrouteservice `driving-hgv`. Free plan is about 2,000 directions and 500 matrix calls a day; one matrix call can hold up to 3,500 origin x destination cells. |

**Cache every API response** to `data/cache/` and commit it. Builds then need no API keys and give identical results everywhere, including in CI.

### Synthetic data (generated)

Config lives in `config/assumptions.yaml`. Seeded so every run is identical.

- **Fleet:** about 20 trucks. Phoenix linehaul tractors (Phoenix DC to Tucson yard and back) and Tucson local tractors (Tucson yard to customers).
- **Routes:** Tucson local routes, 8 to 12 customer stops each, built from real commercial coordinates in Tucson.
- **Period:** 12 months of daily operations.
- **Fuel behavior:** each truck burns fuel by miles and an MPG assumption. When the tank drops under a threshold, the "driver" fuels at a station, with behavior that is mostly sensible, sometimes convenient-but-pricey, and occasionally odd (to create exceptions).
- **Prices:** station price on date = EIA weekly regional price + brand offset + station offset + small noise. Partner stations get a discount (cents off per gallon, configurable).

### Make the sources messy on purpose

This is what proves the integration skill. The generator injects:

- Station names that differ across sources ("LOVES #812 TUCSON" vs "Love's Travel Stop" vs a station ID).
- Duplicate fuel card transactions (same card, same minute, same amount).
- Missing or wrong odometer readings on some card swipes.
- Telematics pings at irregular intervals, with occasional GPS gaps.
- Timezone differences between systems. (Arizona does not observe daylight saving time, but a vendor might export in UTC or Pacific. Handle that.)

Because the generator knows the ground truth, tests can measure accuracy, for example "station matching correctly linked 98.7% of fills."

---

## 4. Unified schema (DuckDB)

```
dim_station(station_id, name, brand, lat, lon, is_partner, partner_discount_cpg, source_ids_json)
dim_truck(truck_id, home_yard, role, tank_gal, mpg_assumed)
dim_route(route_id, yard, stops_json, base_miles, base_minutes, geometry_geojson)
fact_fuel_txn(txn_id, truck_id, station_id, ts_local, gallons, price_per_gal, total, odometer, odometer_repaired_flag, source_row)
fact_ping(truck_id, ts_local, lat, lon, odometer)
fact_route_day(route_id, truck_id, date, actual_miles)
ref_eia_weekly(week_start, price_per_gal)
ref_detour(route_id, station_id, insert_after_stop, extra_miles, extra_minutes)
dq_log(check_name, rows_in, rows_out, issue_count, notes)
```

`dq_log` feeds the "Data hub" page so the cleanup work is visible.

---

## 5. Pages on the site (v1)

All pages show a SYNTHETIC DATA banner and a link to the assumptions.

1. **Overview.** One sentence on the problem, then the **lead insight: the Tucson partnership scout** (section 7), labeled as a synthetic scenario. It shows the best Tucson-area station to sign as a fuel partner, with a slider for the partner discount X (cents per gallon) and the gallons a month and annual net savings that discount would bring. **Headlines are rates** (Cass, Nov 17): the scout leads with net savings in cents per captured gallon at the slider discount, and missed savings lead with cents per card gallon; dollar totals and per-truck-per-year figures are secondary lines. Below it, four KPI tiles (gallons, with yard gallons shown separately from card gallons; average net price of card fills; missed savings in cents per card gallon, then dollars and per truck per year; "Tucson card gallons at partner pricing", linked to the scout), an **"Assumptions behind these numbers"** panel listing every assumption that moves the headline numbers, and links to each view. The scout's totals also show "per truck per year". **(Cass, Nov 17 review)** The scout headline reads "saved per gallon moved to this station". The method paragraph and the "already available at posted prices" figure live behind a "How this is calculated" link, not on the headline card. Why it leads: with few truck-capable stations in Tucson, missed savings there are small, so the bigger lever is creating a cheaper option by negotiating one.
2. **Station map.** Stations colored by average net price paid. Partner stations get a distinct marker. Clicking one opens price history (price paid vs EIA regional average, weekly) and a "break-even" line: *a detour to this station is only worth it if it is at least X cents a gallon cheaper* (section 6).
3. **Test route.** Pick a Tucson route. See its base route on the map. Click any station to insert it and see added miles, added minutes, and net cost or savings for a typical fill. The tool highlights the best stop and explains why in one plain sentence. **(Cass, Nov 17 review)** The page opens on the route where adding a station saves the most. Detours are drawn along real roads (openrouteservice directions, cached). Each option shows **one** cost, for the planned weekday time its stop happens, with one short note on the rush-hour factor; off-peak and rush-hour figures are never shown side by side.
4. **Missed savings.** A table of fills where a reachable alternative would have saved money **after** detour costs, sorted by dollars. Click a row to see both stations on the map with the two route options drawn along real roads. Each row's saving is shown as its parts: price gap x gallons, plus the detour cost of the station actually used, minus the detour cost to the alternative. The headline is missed savings in cents per card gallon, with the weekly total, dollars and a "per truck per year" figure as secondary lines, and the same "Assumptions behind these numbers" panel as the Overview. Only fuel card fills appear; Phoenix DC yard fills never do.
5. **Data hub.** The three source feeds on the left, the one clean table on the right, and the data-quality results in between (matches, duplicates removed, odometers repaired, timezone fixes). This page is the "your apps don't talk to each other" answer.

---

## 6. The net-savings math (the credibility core)

For a fill of `G` gallons on route `r`:

```
cost(station s) = G * p_s
                + (detour_miles_s / mpg) * p_s
                + (detour_minutes_s / 60) * driver_cost_per_hour
                + detour_miles_s * variable_cost_per_mile      (optional)
```

- `p_s` = price at station s on that date, net of any partner discount.
- So a missed saving is `cost(actual) - cost(a)` = price gap x G, plus the detour cost of the station actually used, minus the detour cost to `a` (all real costs: fuel burned, driver time, wear).
- `detour_*` = extra distance and time versus the route without that stop (from `ref_detour`).
- A fill counts as a **missed saving** if some alternative `a` is feasible and `cost(actual) - cost(a) > min_saving_threshold`.
- **Feasible** means the truck has enough fuel to reach `a` with the reserve kept, and the stop fits within the route day.

**Break-even price gap** shown on each station:

```
gap_needed = [ (detour_miles / mpg) * p
             + (detour_minutes / 60) * driver_cost_per_hour
             + detour_miles * variable_cost_per_mile ] / G      (wear term on whenever it is on in cost())
```

**Time costs (Cass, Nov 17, "time-cost upgrade").** The time terms above use *effective* detour minutes and three guardrails, all from `time_cost.*` in config:

```
m(t)            = TTI   if the fill time t falls in a peak window on a weekday (time_cost.peak_windows)
                = PTI   instead of TTI when time_cost.use_reliability_buffer is on (conservative setting)
                = 1     otherwise (off-peak stays at the openrouteservice time)
minutes_eff     = detour_minutes * m(t)

time cost       = minutes_eff * w / 60
feasible also needs minutes_eff <= max_detour_minutes      (15 minutes, Cass, Nov 17 review)

ranking penalty = minutes_eff * schedule_risk_usd_per_minute   (Test route ranking only, never a cost)
                  only when the stop is inserted before the route's last customer delivery
```

- TTI is Tucson's Travel Time Index from the Texas A&M Transportation Institute 2025 Urban Mobility Report (1.17, freeways and arterial streets, where fuel-stop detours run). FHWA's Urban Congestion Report (1.07, freeways only) is shown as the cross-check. PTI is 1.38 in both. Each is stored in config with its source, report year and URL, and shown on the Assumptions panel (Cass, Nov 17 review, decision T1).
- No live traffic feed is used. Commercial traffic APIs restrict caching and derived data (see README, "How this would run in production").
- **Schedule risk is a ranking penalty only** (Cass, Nov 17 review). It ranks Test route stops before the last delivery lower ("Stops before the last delivery are ranked lower to protect the delivery schedule."). It is never a dollar cost, so it is never counted in missed savings, break-even, the scout, or any dollar figure on the Test route.
- The time of a stop: a missed-savings fill uses its own local time. A Test route option and a station's break-even use the planned weekday time of the stop it is inserted after (`planned_arrival` plus unloading time), from the route plans.
- **No overtime term** (Cass, Nov 17). Federal overtime is weekly, and many drivers are exempt under FLSA section 13(b)(1), so a per-detour overtime multiplier is not defensible. Instead there is an **hours-of-service check** (FMCSA, property-carrying, short-haul):
  - Every route day is checked with its worst allowed fuel stop added: driving time of at most `hos.max_driving_hours` (11), and an on-duty window of at most `hos.max_on_duty_hours` (14).
  - Driving time is the route's road minutes plus a `time_cost.max_detour_minutes` detour. On-duty time is the route's `base_minutes` plus that detour plus `fueling.fill_minutes`.
  - Any route day over either limit is flagged. If none are, the site shows "All routes within federal hours-of-service limits".

Break-even and net savings always use **the same cost components** (Cass, Nov 17): the break-even gap is exactly the price difference at which `cost(actual) = cost(alternative)` for a fill of `G` gallons.

**Partnership scout: value the deal creates** (Cass, Nov 17, decision R1). For candidate station `s` at partner discount `X` cents a gallon, each fill `f` of `G` gallons gets:

```
gap_X(f) = cost(actual) - cost(s at posted price minus X)       (section 6 cost, detours included)
gap_0(f) = the same at X = 0

fill already at s:                 deal value = G * X / 100
fill elsewhere that moves at X:    deal value = min( gap_X - max(gap_0, 0),  G * X / 100 )
  ("moves" = feasible and gap_X > min_saving_threshold)
```

The deal is credited only with savings it creates, and never more than the discount on the gallons it captures. So value per captured gallon can never exceed `X`. Any saving that existed at posted prices is not deal value. Large gaps already show as missed savings, and small gaps below the threshold are not shown.

All inputs (`mpg`, `driver_cost_per_hour`, `variable_cost_per_mile`, `min_saving_threshold`, reserve gallons, typical fill size: 72 gallons, a Tucson truck refueling from 35% to 95% of its 120-gallon tank) live in `config/assumptions.yaml` and are printed on the site. They are assumptions, not facts about any real fleet, and the page says so.

---

## 7. Additions beyond what was asked (phased)

| Phase | Feature | Why it's worth it |
|---|---|---|
| **v1** (pulled forward Nov 16, Tucson scope) | **Partnership scout.** For each non-partner, truck-confirmed station in the Tucson box, estimate the gallons that would shift to it, and the annual **net** savings (SPEC section 6 formula, detours included), if it offered a partner discount of X cents. Ranked list plus a slider for X. It is the lead insight on the Overview page and is labeled a synthetic scenario. The full corridor-wide version stays in 1.1. | Fleets looking for new fuel partners can use their own fueling pattern as a negotiating case: "we'd send you about N gallons a month." Cheap to build once `ref_detour` exists. |
| **1.1** | **Fuel card controls review.** Flags for review, not accusations: fill larger than tank capacity, card swipe where the truck's GPS was not near the station, two fills close together with few miles between, MPG far off the truck's normal. | Only possible *because* the data is joined, so it proves the unified-data point. It is also your audit background applied naturally. Label everything "for review," by truck ID only. |
| **2** | **Weekly fuel brief.** A GitHub Action runs weekly, computes the numbers, and has Claude write a short plain-English summary around them. A test checks that every number in the text exists in the data. | The AI piece, done safely. A static site can't hold an API key, so a live chat assistant would need a backend. A build-time brief needs only a GitHub Secret. |
| **3** | **Bring your own data.** Upload a fuel card CSV in a documented template. Processing happens in the browser and nothing is sent anywhere. | Lets a fleet test it on real data without the data ever leaving their computer. |
| **3** | **Tucson yard fueling what-if.** Compare retail and partner stations vs mobile fueling at the yard overnight (local providers exist in Tucson) vs a yard tank. Sliders for the per-gallon premium and driver minutes saved. | Business-case thinking about their actual Tucson situation. All prices are labeled assumptions. |

### Deliberately left out

- **A full route optimizer.** Fleets this size already use commercial routing software. Competing with it is not the point.
- **Driver scorecards or rankings.** Feels like surveillance to drivers and the people who manage them. Keep everything at truck and route level.
- **Live GPS or a mobile app.** Out of scope for a demo, and nothing to show without real data.

---

## 8. Repo layout

```
fleet-fuel-hub/
  README.md               problem, demo link, video link, how it works, limits, AI disclosure
  SPEC.md                 this file
  CLAUDE.md               instructions for Claude Code
  pyproject.toml / uv.lock
  .env.example            ORS_API_KEY=, EIA_API_KEY=  (real .env is gitignored)
  config/assumptions.yaml
  src/fleetfuel/
    fetch/                eia.py, osm.py, ors.py (all cache to data/cache/)
    synth/                generator.py, messiness.py
    ingest/               fuel_card.py, telematics.py, route_plans.py
    normalize/            station_match.py, dedupe.py, odometer.py, timezones.py
    analytics/            price_history.py, net_savings.py, missed.py, partner_scout.py, exceptions.py
    export/               to_site.py
    cli.py                `uv run fleetfuel build` runs the whole pipeline
  data/
    cache/                committed API responses
    raw/                  generated and fetched inputs (committed, small)
    hub.duckdb            gitignored, rebuilt by the pipeline
  site/
    index.html, map.html, route.html, missed.html, hub.html
    js/, css/, data/      (data/ is written by export)
  tests/
  .github/workflows/
    ci.yml                tests on every push
    pages.yml             build pipeline from cache, publish site/
```

---

## 9. Build order and dates

Today is Mon Nov 16. Thanksgiving is Thu Nov 26. Session B ends Dec 4.

| Milestone | Target | Done when |
|---|---|---|
| **M0 Setup** | Mon Nov 16 to Tue Nov 17 | Repo on GitHub, CLAUDE.md and SPEC.md committed, keys in `.env`, `uv run pytest` passes on an empty test. |
| **M1 Real data** | Wed Nov 18 | EIA series and OSM stations fetched and cached; stations hand-checked on a quick map. |
| **M2 Synthetic data and hub** | Thu Nov 19 to Sat Nov 21 | Generator writes three messy sources; ingest and normalize load `hub.duckdb`; matching accuracy test passes; `dq_log` filled. |
| **M3 Routing and map** | Sun Nov 22 to Mon Nov 23 | Routes and `ref_detour` computed and cached; Station map page live on GitHub Pages. |
| **M4 Test route and missed savings** | Tue Nov 24 to Sat Nov 28 | Both pages working; net-savings tests pass; Data hub page done. |
| **M5 Polish and send** | Sun Nov 29 to Tue Dec 1 | README with honest limits section, 90-second walkthrough video, SYNTHETIC labels checked on every page. Send the link. |
| **Phase 1.1** | Week of Dec 7 | Corridor-wide partnership scout (the Tucson version ships in v1) and controls review shipped. |

---

## 10. Definition of done for v1

- [ ] Site loads from the GitHub Pages link on a phone and a laptop.
- [ ] Every page shows SYNTHETIC DATA and links to the assumptions.
- [ ] No company name, logo or private detail anywhere in the repo or site.
- [ ] Clicking a station on the Test route page updates miles, minutes and net cost.
- [ ] The Missed savings total matches a hand-checked calculation for 3 sample fills.
- [ ] `uv run fleetfuel build` works on a fresh clone with no API keys (from cache).
- [ ] CI is green.
- [ ] README has: the problem, demo link, video, architecture diagram, assumptions, limits, and an AI-assistance disclosure.
- [ ] Walkthrough video under 2 minutes.

## 11. Open questions for later

- Which exports would a fleet want it to read (fuel card provider, telematics, routing software)? This shapes the bring-your-own-data template.
- Is a partner discount usually fixed cents off, or cost-plus pricing?

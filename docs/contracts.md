# Contracts: the interface every agent builds against

Version 1.17 (Nov 18 2026, short value badges and a "Start here" page). Owner: the orchestrator.

> **v1.11 summary (read section 11 first):** schedule risk is a ranking penalty only; one cost per Test route option at the time its stop happens; detours drawn on real roads (`detour_paths.geojson`); missed-savings rows carry their cost breakdown; `stops_json` stops carry `planned_arrival`.
SPEC.md is the source of truth for *what* to build. This file is the source of truth
for *how the pieces connect*: file formats, column names and types, the DuckDB schema,
site data formats, CLI entry points, and who owns which folder.

If you are a builder and something here is wrong, ambiguous or missing, **do not work
around it in another agent's folder**. Build to the contract as written, and list the
problem in your report. The orchestrator updates this file and logs the change in section 10.

---

## 1. Ground rules for every builder

1. Edit only the paths you own (section 2). Everything else is read-only to you.
2. Read every parameter through `fleetfuel.config` (section 3). Never edit `config/assumptions.yaml`. Propose new keys in your report.
3. Do not add dependencies or edit `pyproject.toml` / `uv.lock`. Report what you need.
4. If an input owned by another agent does not exist yet, build and test against your own fixtures in `tests/fixtures/<your-area>/`, in this exact format.
5. Tests run offline, without API keys, and never write to `data/hub.duckdb` (use `tmp_path`).
6. Do not edit `tests/conftest.py`, and do not create one. Define pytest fixtures inside your own test files.
7. No em dashes, no real company names (public OSM station names and brands that come from the data are allowed), and no people's names. The one exception (CLAUDE.md rule 2, v1.11) is the owner's author credit "Built by Cass Bowes" in the README, LICENSE and site sidebar footer. Customer stops are only ever `CUST-####` / "Customer 014".
8. All CSVs are UTF-8, comma-separated, with a header row, `\n` line endings and no index column. Floats use `.` as the decimal point. Booleans in CSVs are written `true` / `false`.

## 2. Folder ownership

| Owner | Owns (may edit) | Wave |
|---|---|---|
| orchestrator | `CLAUDE.md`, `SPEC.md`, `README.md`, `docs/`, `config/`, `.claude/`, `scripts/`, `pyproject.toml`, `uv.lock`, `.gitignore`, `.gitattributes`, `.env.example`, `src/fleetfuel/__init__.py`, `src/fleetfuel/cli.py`, `src/fleetfuel/config.py`, `tests/test_smoke.py`, `tests/test_config.py`, `tests/test_repo_rules.py` | all |
| data-fetcher | `src/fleetfuel/fetch/`, `src/fleetfuel/stations.py`, `tests/test_stations_*.py`, `data/cache/eia/`, `data/cache/osm/`, `data/raw/eia_diesel.csv`, `data/raw/stations.geojson`, `data/raw/customer_points.csv`, `tests/test_fetch_*.py`, `tests/fixtures/fetch/` | 1 |
| synth-builder | `src/fleetfuel/synth/`, `data/raw/fleet.csv`, `data/raw/partner_program.csv`, `data/raw/station_prices.csv`, `data/raw/fuel_card.csv`, `data/raw/telematics.csv`, `data/raw/route_plans.csv`, `data/raw/yard_fuel.csv`, `data/raw/ground_truth/`, `tests/test_synth_*.py`, `tests/fixtures/synth/` | 1 |
| pipeline-builder | `src/fleetfuel/ingest/`, `src/fleetfuel/normalize/`, `src/fleetfuel/db.py`, `tests/test_ingest_*.py`, `tests/test_normalize_*.py`, `tests/test_db_*.py`, `tests/fixtures/pipeline/` | 1 |
| site-builder | `site/` except `site/data/` from Wave 2 on (in Wave 1 it held placeholder files there), `.github/workflows/`, `tests/test_site_*.py`, `tests/fixtures/site/` | 1 |
| routing-engineer | `src/fleetfuel/routing/`, `data/cache/ors/`, `tests/test_routing_*.py`, `tests/fixtures/routing/` | 2 |
| analytics-builder | `src/fleetfuel/analytics/`, `src/fleetfuel/export/`, `site/data/` (from Wave 2), `tests/test_analytics_*.py`, `tests/test_export_*.py`, `tests/fixtures/analytics/` | 2 |
| qa-reviewer | nothing (read-only reviewer) | all |

## 3. Shared code you can import (owned by the orchestrator)

`fleetfuel.config`:

| Name | What it is |
|---|---|
| `REPO_ROOT`, `DATA_DIR`, `RAW_DIR`, `GROUND_TRUTH_DIR`, `CACHE_DIR`, `DB_PATH`, `SITE_DIR`, `SITE_DATA_DIR` | `pathlib.Path` constants for the standard locations (relative to the checkout, so they work in worktrees) |
| `load_assumptions(path=None) -> dict` | the parsed `config/assumptions.yaml` |
| `get(cfg, "savings.reserve_gal")` | one value by dotted key; raises `KeyError` naming the key if it is missing |
| `describe_assumptions(path=None) -> list[dict]` | every leaf as `{"key", "value", "note"}` in file order (for the site) |
| `load_env()` / `has_key("EIA_API_KEY")` | load `.env` from the checkout or any parent folder; check that a key is set without revealing it |

## 4. Files in `data/raw/`

Types: `str`, `int`, `float`, `bool`, `date` = `YYYY-MM-DD`, `datetime` = `YYYY-MM-DD HH:MM:SS`.
"Nullable" means the cell may be empty.

### 4.1 `eia_diesel.csv` (data-fetcher, REAL)

Weekly retail on-highway No. 2 diesel, West Coast (PADD 5) except California. One row per week, sorted by `week_start` ascending, with no duplicate weeks, from `fetch.eia.start_date` to the latest available week.

| column | type | notes |
|---|---|---|
| `week_start` | date | the EIA `period` value |
| `price_per_gal` | float | USD per gallon, 3 decimals |
| `series_id` | str | the EIA series ID, discovered through API metadata (same value on every row) |

### 4.2 `stations.geojson` (data-fetcher, REAL)

A GeoJSON `FeatureCollection` of `Point` features, `coordinates: [lon, lat]` with 6 decimals. It holds the stations that pass the SPEC section 3 filter: truck-capable stations in `fetch.overpass.stations_bbox`, plus, inside `fetch.overpass.tucson_metro_bbox` only, cardlock or fleet fueling sites and diesel stations with truck-access evidence. Stations whose name, brand or operator matches a forbidden term are dropped before IDs are assigned and never written to the cache (section 4.11). Manual overrides are **not** applied in this file; downstream code applies them through `fleetfuel.stations` (4.11). Features are sorted by `station_id`.

| property | type | notes |
|---|---|---|
| `station_id` | str | `STN-001`, `STN-002`, ... assigned in order of `osm_ref` sorted as text, after dedupe. Stable as long as the cache is unchanged. |
| `osm_ref` | str | `node/123`, `way/456` or `relation/789` |
| `name` | str, nullable | OSM `name` |
| `brand` | str, nullable | OSM `brand` |
| `operator` | str, nullable | OSM `operator` |
| `city` | str, nullable | OSM `addr:city` |
| `truck_tagged` | bool | `hgv=yes`, `hgv=designated` or `fuel:HGV_diesel=yes` |
| `truck_brand` | bool | brand or name matches `fetch.overpass.truck_brand_aliases` |
| `station_class` | str | `travel_center` if `truck_brand`, else `cardlock` if `access_basis` is `cardlock`, else `branded` if `brand` is set, else `unbranded`. This drives `prices.class_offset_per_gal`. |
| `truck_access` | str | `confirmed` or `unverified` (rules below) |
| `access_basis` | str | the strongest rule that kept it: `truck_tag`, `truck_brand`, `cardlock`, `fleet_access`, `truck_evidence` or `diesel_only` (`chain_locator` is set only by an `add` override, 4.11) |

`truck_access` rules (first match wins):
- `confirmed` when `truck_tagged` (`hgv=yes|designated` or `fuel:HGV_diesel=yes`), or `truck_brand`, or `cardlock` (brand, name or operator matches `fetch.overpass.cardlock_aliases`), or `fleet_access` (`access` in `fetch.overpass.fleet_access_values` with `fuel:diesel=yes`). The last two apply inside `tucson_metro_bbox` only.
- `unverified` when, inside `tucson_metro_bbox`, the station has `fuel:diesel=yes` plus weaker evidence: `hgv` in `fetch.overpass.truck_evidence_hgv_values`, or a word from `fetch.overpass.truck_evidence_words` in its name or operator (`access_basis` = `truck_evidence`).
- `unverified` when, inside `tucson_metro_bbox`, the station has `fuel:diesel=yes` and no other evidence (`access_basis` = `diesel_only`). Added in v1.5 per Cass's D0 answer.
- Chain-locator confirmations are not in this file; they come from `add` entries in the overrides (4.11).

### 4.3 `customer_points.csv` (data-fetcher, REAL coordinates, anonymized)

Coordinates of Tucson food-service businesses, used as stand-in customer locations. **No names, addresses, OSM IDs or tags anywhere on disk, including the cache.** There are at most `fetch.customers.max_points` rows, sorted by `customer_id`.

| column | type | notes |
|---|---|---|
| `customer_id` | str | `CUST-0001` and up |
| `lat` | float | `fetch.customers.coord_decimals` decimals |
| `lon` | float | same |

Display label everywhere: `"Customer " + the last 3 digits of the number`, zero-padded (`CUST-0014` displays as `Customer 014`).

### 4.4 `fleet.csv` (synth-builder, clean master data)

| column | type | notes |
|---|---|---|
| `truck_id` | str | `PHX-01`... for linehaul, `TUC-01`... for local |
| `home_yard` | str | `PHX_DC` or `TUC_YARD` |
| `role` | str | `linehaul` or `local` |
| `tank_gal` | float | from `fleet.*_tank_gal` |
| `mpg_assumed` | float | from `fleet.*_mpg` |
| `telematics_device_id` | str | `GPS-` + 5 digits. This is the only key telematics uses, so this file is the crosswalk. |
| `fuel_card_last4` | str | 4 digits, unique per truck |

### 4.5 `partner_program.csv` (synth-builder)

The hypothetical distributor's fuel partner list. Partner designations are **synthetic** and imply nothing about any real station. Since v1.7 the list is a stated assumption, not a random draw: every truck-confirmed station (after overrides) inside `prices.partner_set_bbox`, described by `prices.partner_set_description`, each with `prices.partner_discount_cpg`. Since v1.8, with `prices.partner_set_named_only: true`, only stations with an OSM name or brand count (named truck stops; unnamed truck-tagged points are left out).

| column | type | notes |
|---|---|---|
| `station_id` | str | from 4.2 |
| `partner_discount_cpg` | float | cents per gallon off the posted price |

### 4.6 `station_prices.csv` (synth-builder, stand-in for a posted price feed)

One row per station per calendar day in `period`, sorted by `date` then `station_id`. Analytics uses it to price alternatives a truck did *not* use.

| column | type | notes |
|---|---|---|
| `date` | date | |
| `station_id` | str | |
| `posted_price_per_gal` | float | pump price before any partner discount, 3 decimals |

### 4.7 `fuel_card.csv` (synth-builder, MESSY, like a fuel card vendor export)

One row per card swipe, sorted by `TransDate` then `TransTime`. The column names are vendor-style on purpose.

| column | type | notes |
|---|---|---|
| `TransactionID` | str | `FC` + 7 digits, unique per row (duplicate swipes get their own ID) |
| `CardLast4` | str | matches `fleet.fuel_card_last4` |
| `Unit` | str | `truck_id` |
| `TransDate` | str | `MM/DD/YYYY` |
| `TransTime` | str | `HH:MM`, local Arizona time |
| `MerchantName` | str | messy: canonical, `upper_store`, `short_brand` or `merchant_code` variant. A `merchant_code` is exactly `MERCH ` plus 6 digits (for example `MERCH 004512`); synth writes it and the pipeline's code-versus-name filing relies on it. A station with no OSM name or brand shows `messiness.unnamed_station_merchant_label` plus a stable store number (`FUEL STOP #4471`), never an internal station ID. |
| `MerchantCity` | str, nullable | |
| `MerchantState` | str | `AZ` |
| `Product` | str | `ULSD` |
| `Gallons` | float | 3 decimals |
| `PPG` | float | price paid per gallon, **net of any partner discount**, 3 decimals |
| `Total` | float | `Gallons * PPG`, 2 decimals |
| `Odometer` | str, nullable | driver-entered miles: usually right, sometimes blank, sometimes wrong |

### 4.8 `telematics.csv` (synth-builder, MESSY, like a GPS/ELD export)

Pings while a truck is moving, at irregular intervals with occasional gaps, sorted by `device_id` then `event_time`. Paths are straight lines between stops in Wave 1.

| column | type | notes |
|---|---|---|
| `device_id` | str | `telematics_device_id` from 4.4 |
| `event_time` | str | `YYYY-MM-DD HH:MM:SS` in **UTC**, with no offset marker (the vendor documents it as UTC) |
| `lat` | float | 5 decimals |
| `lon` | float | 5 decimals |
| `odometer_mi` | float | 1 decimal; reliable, increases monotonically per device |
| `speed_mph` | int | |

### 4.9 `route_plans.csv` (synth-builder, like a routing software export)

One row per planned stop per route per day, sorted by `PlanDate`, `RouteID`, `StopSeq`. The stop lists for each `RouteID` are fixed for the whole period.

| column | type | notes |
|---|---|---|
| `PlanDate` | date | |
| `RouteID` | str | `TUC-R01`..`TUC-R10` (local), `PHX-L01`..`PHX-L05` (linehaul, one per daily run; the count is `routes.linehaul_runs_per_day`) |
| `Vehicle` | str | `truck_id` |
| `StopSeq` | int | 0 = leave the yard; the last stop = return to the yard |
| `StopType` | str | `yard` or `customer` |
| `LocationID` | str | `PHX_DC`, `TUC_YARD` or `CUST-####` |
| `Lat` | float | 5 decimals |
| `Lon` | float | 5 decimals |
| `PlannedArrival` | str | `HH:MM` local Arizona time |
| `PlannedMilesFromPrev` | float | 1 decimal, 0 for `StopSeq` 0 |

Every linehaul route is `PHX_DC` (0), then `TUC_YARD` (1), then `PHX_DC` (2). The runs share the same stops and are separate routes so each run has one vehicle per day.

### 4.10 `ground_truth/` (synth-builder)

What the generator actually did. Only the evaluation step reads this, never the pipeline's cleaning logic.

**`fuel_txn_truth.csv`**: exactly one row per `fuel_card.csv` data row.

| column | type | notes |
|---|---|---|
| `source_row` | int | 1-based data row number in `fuel_card.csv` (the header is row 0) |
| `TransactionID` | str | same as in `fuel_card.csv` |
| `true_event_id` | str | the real fill event; a duplicate shares its original's ID |
| `is_duplicate` | bool | true only for the injected copy |
| `truck_id` | str | |
| `true_station_id` | str | |
| `true_ts_local` | datetime | Arizona local time |
| `true_odometer` | float | |
| `odometer_issue` | str | `none`, `missing` or `wrong` |
| `name_variant` | str | `canonical`, `upper_store`, `short_brand` or `merchant_code` |
| `fill_behavior` | str | `sensible`, `convenient` or `odd` |
| `route_id` | str, nullable | route being run when the truck fueled |

**`injected_issues.csv`**: one row per injected problem.

| column | type | notes |
|---|---|---|
| `issue_id` | str | `ISS-00001` and up |
| `issue_type` | str | `name_variant`, `duplicate_swipe`, `missing_odometer`, `wrong_odometer`, `irregular_ping`, `gps_gap` or `timezone_utc` |
| `source_file` | str | `fuel_card.csv`, `telematics.csv` or `route_plans.csv` |
| `source_row` | int, nullable | for row-level issues |
| `truck_id` | str, nullable | |
| `start_ts_local` | datetime, nullable | for time-window issues (GPS gaps) |
| `end_ts_local` | datetime, nullable | |
| `details` | str | short plain-English description |

`irregular_ping` and `timezone_utc` are one summary row each for the whole file.

**`run_manifest.json`**: `{"seed": int, "period_start": date, "period_end": date, "row_counts": {"<file>": int, ...}, "inputs": {"stations": "real" | "fixture", "customers": "real" | "fixture", "eia": "real" | "fixture"}}`. There is no timestamp, so reruns stay byte-identical.

### 4.12 `yard_fuel.csv` (synth-builder, v1.6, clean: the Phoenix DC tank log)

One row per on-site fill at a yard with `onsite_fuel: true` (only `PHX_DC`), sorted by `FillDate`, `FillTime`, `Unit`. These are **not** fuel card transactions: they never appear in `fuel_card.csv`, ground truth, missed savings or the partner scout.

| column | type | notes |
|---|---|---|
| `FillDate` | date | |
| `FillTime` | str | `HH:MM`, local Arizona time, before the run's departure |
| `Yard` | str | `PHX_DC` |
| `Unit` | str | `truck_id` |
| `Gallons` | float | 3 decimals |
| `PricePerGal` | float | the EIA weekly price for that week minus `yard_fuel.discount_vs_eia_per_gal`, 3 decimals |
| `Total` | float | `Gallons * PricePerGal`, 2 decimals |

**Required whenever any yard has `onsite_fuel: true`.** Ingest, analytics and export raise a plain-English error naming the file if it, or `fact_yard_fuel`, is missing in that case. The file may be absent only when no yard has a tank. There is no silent zero (CLAUDE.md rule 5). Linehaul tractors fill here before each departure, except a share `yard_fuel.skip_rate` of departures. A linehaul card fill then happens only if the truck runs low mid-trip, under the usual refuel rule. `run_manifest.json` `row_counts` includes this file.

### 4.11 Station overrides (`config/station_overrides.yaml`, orchestrator) and `fleetfuel.stations` (data-fetcher)

`config/station_overrides.yaml` is the **only** place for manual station fixes. Top-level key `overrides:` holds a list of entries:

| field | type | notes |
|---|---|---|
| `osm_ref` | str, nullable | **the stable key** for `exclude`, `merge_into` and `rename` (station IDs renumber when the filter changes). Empty for `add`. |
| `add_id` | str, nullable | required for `add`: the stable key `A01`, `A02`, ... |
| `name`, `brand` | str | `add` only: as the chain's locator shows them (`brand` nullable) |
| `lat`, `lon` | float | `add` only: from the chain's own locator or store page, 6 decimals |
| `source_url` | str | `add` only: the authoritative public page that shows diesel or truck lanes at this site |
| `city` | str, nullable | `add` only |
| `station_id` | str, nullable | the ID when the entry was written, for readers; not used for matching |
| `action` | str | `exclude`, `merge_into`, `rename` or `add` |
| `target_osm_ref` | str, nullable | required for `merge_into`: the station that survives |
| `new_name` | str, nullable | required for `rename` |
| `reason` | str | one plain-English line; `name conflict` for forbidden-term exclusions (no name is ever written) |

Semantics: `exclude` drops the station. `merge_into` drops the source station, keeps the target's `station_id`, fills the target's empty `name`, `brand`, `operator` and `city` from the source, ORs `truck_tagged` and `truck_brand`, keeps the stronger `truck_access` and recomputes `station_class`, and adds the source `osm_ref` to `source_ids_json`. `rename` replaces `name`. An entry whose `osm_ref` is not in `stations.geojson` is reported, not an error (name-conflict stations are never in that file).

`add` (v1.5) records a truck-capable site confirmed from a chain's or cardlock network's own public locator. Applied after the other actions. If an existing station lies within `stations.add_match_distance_m` of the entry's coordinates, that station keeps its ID and becomes `truck_access` = `confirmed` with `access_basis` = `chain_locator`; its empty `name`, `brand` and `city` are filled from the entry. Otherwise a new station is created with `station_id` = `STN-` + `add_id` (for example `STN-A01`), `osm_ref` null, `station_class` from the usual rule where the brand or name matching `fetch.overpass.cardlock_aliases` counts as `cardlock` (and a truck-stop brand as `travel_center`), so a cardlock network site is classed `cardlock`, `truck_access` = `confirmed` and `access_basis` = `chain_locator`. Either way `source_ids_json` gets `"source_url"` and `"add_id"`. The applied record's `status` is `applied_new` or `applied_matched`, and `target_station_id` names the station it landed on. Only Cass's authoritative-source rule (SPEC section 3) qualifies a site for `add`; never satellite imagery.

`fleetfuel.stations` (shared, importable by synth, pipeline, analytics and export):

| Name | What it is |
|---|---|
| `load_overrides(path=None) -> list[dict]` | parsed and validated entries |
| `load_stations(raw_dir=None, overrides_path=None) -> tuple[pd.DataFrame, list[dict]]` | `stations.geojson` as a frame (columns: every 4.2 property plus `lat`, `lon` and `merged_osm_refs`, a list of the osm_refs folded in by `merge_into`, which the pipeline writes into `source_ids_json`), with overrides applied, plus one applied-override record per entry: `{"osm_ref", "station_id", "action", "target_station_id", "reason", "status": "applied" | "not_found"}` |
| `display_name(row) -> str` | the name, else the brand, else `"Unnamed fuel station · STN-001"` |

Synth and the pipeline **must** read stations only through `load_stations`. The pipeline writes one `dq_log` row per override record (`check_name` = `station_override`, `notes` = action, IDs and reason).

## 5. `data/cache/`

Committed, so a fresh clone builds with no API keys.

- `data/cache/eia/`: raw EIA responses as JSON, one file per request, named by a short hash of the request **with the API key removed**, plus `series_meta.json` (`{"route", "facets", "series_id", "description", "units", "fetched_on"}`).
- `data/cache/osm/`: `stations_overpass.json` and `customers_points.json`. The stations file holds the Overpass response **filtered to the elements that pass the truck filter** (section 4.2), with their tags. This is a deliberate exception to "cache the raw response": the full response included non-truck stations whose names would break CLAUDE.md rule 2, and they are never used. The customers file is **sanitized before it is written**: a list of `[lat, lon]` pairs only, with no IDs and no tags. This is a deliberate exception to "cache the raw response", made to keep customers anonymous (CLAUDE.md rule 3).
- `data/cache/ors/` (Wave 2): one JSON file per directions or matrix request, named by a hash of the request body with no key.

Rule: read the cache first. Call the network only on a cache miss or `--refresh`. Never write a key into any cached file.

## 6. DuckDB schema (`data/hub.duckdb`)

Built by `fleetfuel.db.create_schema()` (drop and recreate, so the build is idempotent). All timestamps are **naive America/Phoenix local time**. These columns go beyond SPEC section 4 and are marked `-- added`: the match confidence fields, `ref_station_price`, and the `dq_*` / `eval_*` detail tables.

```sql
CREATE TABLE dim_station (
    station_id           VARCHAR PRIMARY KEY,   -- STN-###
    name                 VARCHAR,
    brand                VARCHAR,
    lat                  DOUBLE NOT NULL,
    lon                  DOUBLE NOT NULL,
    station_class        VARCHAR NOT NULL,      -- added: travel_center | cardlock | branded | unbranded
    is_partner           BOOLEAN NOT NULL,
    partner_discount_cpg DOUBLE NOT NULL,       -- cents per gallon, 0 for non-partners
    source_ids_json      VARCHAR NOT NULL,      -- JSON: {"osm_ref": "...", "merged_osm_refs": [...], "fuel_card_names": [...], "merchant_codes": [...]}
    truck_access         VARCHAR NOT NULL,      -- added: confirmed | unverified
    access_basis         VARCHAR NOT NULL       -- added: truck_tag | truck_brand | cardlock | fleet_access | truck_evidence | diesel_only | chain_locator
);

CREATE TABLE dim_truck (
    truck_id     VARCHAR PRIMARY KEY,
    home_yard    VARCHAR NOT NULL,              -- PHX_DC | TUC_YARD
    role         VARCHAR NOT NULL,              -- linehaul | local
    tank_gal     DOUBLE NOT NULL,
    mpg_assumed  DOUBLE NOT NULL
);

CREATE TABLE dim_route (
    route_id          VARCHAR PRIMARY KEY,      -- TUC-R01 | PHX-L01 (PHX-L01..PHX-L05)
    yard              VARCHAR NOT NULL,
    stops_json        VARCHAR NOT NULL,         -- JSON list: [{"seq": 0, "location_id": "TUC_YARD", "label": "Tucson yard", "lat": .., "lon": .., "planned_arrival": "05:00"}, ...]  (planned_arrival v1.11: HH:MM from route_plans.csv PlannedArrival on the first plan day)
    base_miles        DOUBLE,                   -- Wave 1: sum of planned miles; Wave 2: road miles
    base_minutes      DOUBLE,                   -- Wave 1: planned drive plus stop minutes; Wave 2: road minutes plus stop minutes
    geometry_geojson  VARCHAR                   -- Wave 1: straight-line LineString through the stops; Wave 2: road geometry
);

CREATE TABLE fact_fuel_txn (
    txn_id                    VARCHAR PRIMARY KEY,  -- fuel card TransactionID of the kept row
    truck_id                  VARCHAR NOT NULL,
    station_id                VARCHAR,              -- NULL when unmatched (never guessed)
    ts_local                  TIMESTAMP NOT NULL,
    gallons                   DOUBLE NOT NULL,
    price_per_gal             DOUBLE NOT NULL,      -- price paid, net of partner discount
    total                     DOUBLE NOT NULL,
    odometer                  DOUBLE,               -- after repair
    odometer_repaired_flag    BOOLEAN NOT NULL,
    source_row                INTEGER NOT NULL,     -- 1-based row in fuel_card.csv
    station_match_confidence  DOUBLE,               -- added: 0 to 1
    station_match_method      VARCHAR               -- added: e.g. name+brand+gps | gps_only | unmatched
);

CREATE TABLE fact_yard_fuel (                    -- added (v1.6): on-site yard fills from yard_fuel.csv, not card transactions
    truck_id       VARCHAR NOT NULL,
    yard           VARCHAR NOT NULL,             -- PHX_DC
    ts_local       TIMESTAMP NOT NULL,
    gallons        DOUBLE NOT NULL,
    price_per_gal  DOUBLE NOT NULL,
    total          DOUBLE NOT NULL
);

CREATE TABLE fact_ping (
    truck_id  VARCHAR NOT NULL,
    ts_local  TIMESTAMP NOT NULL,
    lat       DOUBLE NOT NULL,
    lon       DOUBLE NOT NULL,
    odometer  DOUBLE
);

CREATE TABLE fact_route_day (
    route_id      VARCHAR NOT NULL,
    truck_id      VARCHAR NOT NULL,
    date          DATE NOT NULL,
    actual_miles  DOUBLE                         -- from the telematics odometer for that truck-day
);

CREATE TABLE ref_eia_weekly (
    week_start     DATE PRIMARY KEY,
    price_per_gal  DOUBLE NOT NULL
);

CREATE TABLE ref_station_price (                 -- added: posted price feed (4.6)
    station_id            VARCHAR NOT NULL,
    date                  DATE NOT NULL,
    posted_price_per_gal  DOUBLE NOT NULL
);

CREATE TABLE ref_detour (                        -- filled by routing in Wave 2, empty in Wave 1
    route_id           VARCHAR NOT NULL,
    station_id         VARCHAR NOT NULL,
    insert_after_stop  INTEGER NOT NULL,         -- StopSeq after which the station is inserted
    extra_miles        DOUBLE NOT NULL,
    extra_minutes      DOUBLE NOT NULL
);

CREATE TABLE dq_log (
    check_name   VARCHAR NOT NULL,               -- e.g. ingest_fuel_card, dedupe_swipes, station_match, odometer_repair, timezone_telematics
    rows_in      BIGINT,
    rows_out     BIGINT,
    issue_count  BIGINT,
    notes        VARCHAR                          -- one plain-English sentence for the Data hub page
);

CREATE TABLE dq_duplicate (                      -- added
    source_row       INTEGER NOT NULL,           -- the removed row
    kept_source_row  INTEGER NOT NULL,
    reason           VARCHAR NOT NULL
);

CREATE TABLE dq_station_unmatched (              -- added
    source_row                 INTEGER NOT NULL,
    merchant_name              VARCHAR,
    best_candidate_station_id  VARCHAR,
    best_confidence            DOUBLE,
    reason                     VARCHAR NOT NULL
);

CREATE TABLE eval_accuracy (                     -- added: scored against ground truth, only when it exists
    metric          VARCHAR NOT NULL,            -- station_match_precision | station_match_recall | dedupe_precision | dedupe_recall | odometer_repair_accuracy
    value           DOUBLE NOT NULL,             -- 0 to 1
    true_positive   BIGINT,
    false_positive  BIGINT,
    false_negative  BIGINT,
    notes           VARCHAR
);
```

Metric definitions:
- **Station match precision** = matched rows with the correct station / all matched rows. **Recall** = matched rows with the correct station / all non-duplicate rows.
- **Dedupe precision** = removed rows that are true duplicates / all removed rows. **Recall** = true duplicates removed / all true duplicates.
- **Odometer repair accuracy** = rows with an issue whose repaired value is within `pipeline.odometer.eval_tolerance_mi` of the truth / all rows with an odometer issue.

### 6.1 Analytics working table (created by `analytics.run`, not by `db.create_schema`)

`an_result(name VARCHAR PRIMARY KEY, payload VARCHAR NOT NULL, notes VARCHAR)`: one row per analytics result (for example `missed`, `detours`, `partner_scout`, `price_history`). `payload` is JSON. `notes` is a plain-English line of counts, such as fills skipped and why. `export.run` reads only this table and the contract tables above. Analytics replaces it on every run.

## 7. Site data (`site/data/`)

Written by `export` in Wave 2. In Wave 1, site-builder writes small placeholder files in exactly these shapes, with made-up names. Money is USD with 2 decimals; prices are USD per gallon with 3 decimals; `*_cpg` values are cents per gallon. Every file has a top-level `"synthetic": true`.

**`meta.json`** (every page reads it for the banner and the Assumptions page)
```json
{"synthetic": true, "generated_on": "2026-11-16", "period_start": "2025-11-01", "period_end": "2026-10-31",
 "assumptions": [{"key": "savings.driver_cost_per_hour", "value": 38.0, "note": "Assumption: ..."}],
 "trucks_in_fleet": 20,
 "headline_assumptions": [{"key": "prices.class_offset_per_gal.cardlock", "value": -0.10, "note": "Assumption: ..."}]}
```
`headline_assumptions` (v1.6) holds the entries of `assumptions` named in `site.headline_assumption_keys`, in that order. Since v1.7 a key starting with `derived.` is computed by export from the hub, not read from config, and its entry also has `"source": "derived"` (config entries have `"source": "config"`). The only one so far is `derived.tucson_local_route_miles_per_day`: the mean `dim_route.base_miles` of the `TUC-R..` routes, 1 decimal, with the note (v1.10) "synthetic-route assumption: openrouteservice road miles over the synthetic route plans". The site shows it as "Tucson local route length: 31.6 mi/day (synthetic-route assumption: openrouteservice road miles over the synthetic route plans)", that is, value, unit, then the note in parentheses. An unknown `derived.` key raises. They feed the "Assumptions behind these numbers" panel on the Overview and Missed savings pages. `trucks_in_fleet` is the number of rows in `dim_truck`.

**`overview.json`**
```json
{"synthetic": true, "kpis": {"total_gallons": 0, "card_gallons": 0, "yard_gallons": 0,
  "avg_net_price_per_gal": 0.0, "avg_yard_price_per_gal": 0.0,
  "missed_savings_usd": 0.0, "missed_usd_per_truck_per_year": 0.0, "missed_savings_cpg_card": 0.0,
  "tucson_card_gallons_partner_share": 0.0}}
```
- v1.7: `missed_savings_cpg_card` = `missed_savings_usd` ÷ `card_gallons` × 100, in cents per card gallon, 2 decimals. It is the **headline** figure; dollars and per truck per year are secondary.
- v1.10: `overview.json` also has `hos_check` (SPEC section 6): `{"limit_driving_hours": 11.0, "limit_on_duty_hours": 14.0, "max_driving_hours": 5.8, "max_on_duty_hours": 7.4, "routes_over": [], "message": "All routes within federal hours-of-service limits"}`.
  - Hours are 1 decimal and cover every route (local and linehaul), each with its worst allowed fuel stop added.
  - `routes_over` lists `{"route_id", "driving_hours", "on_duty_hours", "limit"}` for any route over a limit.
  - Each entry of `routes_over` is an object, never a bare ID. `limit` is `"driving"`, `"on_duty"` or `"both"`.
  - When the list is not empty, `message` is exactly "1 route over federal hours-of-service limits" or "N routes over federal hours-of-service limits".
- `tucson_card_gallons_partner_share` (replaces `partner_share_gallons`) = card gallons bought at partner stations by `TUC_YARD` trucks ÷ all card gallons of `TUC_YARD` trucks (0 to 1). The site labels it "Tucson card gallons at partner pricing" and links it to the scout.
- `total_gallons` = `card_gallons` (fuel card fills after dedupe) + `yard_gallons` (`fact_yard_fuel`). The Overview shows yard gallons separately.
- `avg_net_price_per_gal` covers card fills only; `avg_yard_price_per_gal` covers yard fills.
- The three cents-per-gallon rates and `tucson_card_gallons_partner_share` are null (JSON null) when their gallon denominator is 0, so the site shows "n/a" instead of a misleading 0.
- Per truck per year everywhere = the period total × 365 / period days ÷ `trucks_in_fleet`.

**`stations.geojson`**: `FeatureCollection` of Points, `"synthetic": true` at the top level. Properties: `station_id`, `name`, `display_name` (4.11 rule), `city` (nullable, from OSM `addr:city`; the map's location line), `brand`, `station_class`, `truck_access` (`confirmed` | `unverified`; unverified stations draw dimmed), `is_partner` (bool), `partner_discount_cpg`, `fills` (int), `gallons`, `avg_price_paid` (nullable when there are no fills), `avg_posted_price`, `break_even_cpg` (SPEC section 6 gap, using the Tucson route whose detour cost to this station is the median across Tucson routes, so miles and minutes come from the same route, and `savings.typical_fill_gal`; since v1.6 it includes the per-mile wear term whenever `savings.include_variable_cost` is on, the same components as net savings; nullable without detours).

**`price_history.json`**
```json
{"synthetic": true, "eia": [{"week_start": "2025-11-03", "price_per_gal": 3.912}],
 "stations": {"STN-001": [{"week_start": "2025-11-03", "avg_price_paid": 3.85, "avg_posted_price": 3.97, "gallons": 412.5}]}}
```
`avg_price_paid` is null in weeks with no fills.

**EIA attribution (CLAUDE.md rule 11, v1.7).**
- A chart that plots the actual `eia` series carries `Source: U.S. Energy Information Administration (EIA)`.
- Every place that shows synthetic prices, or numbers derived from them (station posted and paid prices, yard prices, prices in samples, tables and tiles, the "below regional avg" chip, cents-per-gallon savings), carries `Synthetic prices built from the EIA weekly diesel average (benchmark source: U.S. Energy Information Administration)` and never the plain source line.
- A chart showing both gets both lines. No EIA logo, and no copy that implies EIA reviewed or endorses this project.

**`routes.geojson`**: `FeatureCollection`. `LineString` features with properties `{"kind": "route", "route_id", "label", "yard", "base_miles", "base_minutes", "stop_count"}`, plus `Point` features with properties `{"kind": "stop", "route_id", "seq", "label"}` (labels are "Tucson yard", "Phoenix DC" or "Customer 014").

**`detours.json`** (Test route page)
```json
{"synthetic": true, "typical_fill_gal": 90, "price_basis": "average posted net price, last 4 weeks of the period",
 "routes": {"TUC-R01": {"baseline_station_id": "STN-004", "best_station_id": "STN-007",
   "best_reason": "Saves $9.80 on a 90 gallon fill even after the 2.1 extra miles and 6 extra minutes.",
   "options": [{"station_id": "STN-007", "insert_after_stop": 3, "extra_miles": 2.1, "extra_minutes": 6.0,
                "price_per_gal": 3.799, "fuel_cost_usd": 341.91, "detour_cost_usd": 5.57,
                "total_cost_usd": 347.48, "net_savings_vs_baseline_usd": 9.80}]}}}
```
`baseline_station_id` is the station that route's trucks used most. `net_savings_vs_baseline_usd` is positive when the option is cheaper **after** detour costs.

**Time costs (v1.9, SPEC section 6).**
- **Options:** the base fields are off-peak, using openrouteservice minutes. Every option also has `extra_minutes_peak` (minutes × the peak multiplier), `detour_cost_peak_usd`, `total_cost_peak_usd` and `net_savings_vs_baseline_peak_usd`. It also has `before_last_delivery` (bool; true means the schedule-risk charge applies) (v1.10: there is no overtime term; see the `hos_check` block in overview.json) `over_max_detour` (bool, judged on off-peak minutes; true means the option is excluded from best-stop picks and from missed savings) and `over_max_detour_peak` (bool, judged on peak minutes; the site flags the peak figure when true).
- **`worked_example`** at the top level: `{"detour_minutes", "detour_miles", "time_local", "is_peak", "multiplier", "effective_minutes", "time_cost_usd", "fuel_cost_usd", "wear_cost_usd", "schedule_risk_usd", "total_usd", "fill_gal", "price_per_gal", "cpg_needed", "sentence"}`.
  - It is built from `time_cost.worked_example_*` and `savings.typical_fill_gal`, at the median basis price of the stations inside `fetch.overpass.tucson_metro_bbox`.
  - The `sentence` is plain English, built from the computed parts, for example "A 30-minute detour at 5 pm costs $33.85 ($20.33 driver time, $11.02 fuel, $2.50 wear), so the station needs to be at least 37.6 cents a gallon cheaper to pay off."
- **`stations.geojson`:** `break_even_cpg` stays off-peak, and a new `break_even_cpg_peak` uses the peak multiplier. Both use the full time-cost rules.
- **Missed savings and the scout** apply the rules per fill, using each fill's own local time. `best_reason` wording (v1.6): when the best stop's extra miles round to under 0.1 and its extra minutes are under 1, it says "with almost no extra driving" instead of quoting the numbers, for example "Saves $1.47 on a 90 gallon fill with almost no extra driving."

Truck access toggle: every option also has `"truck_access": "confirmed" | "unverified"`. `best_station_id` and `best_reason` consider confirmed stations only; each route also has `best_station_id_incl_unverified` and `best_reason_incl_unverified` for when the site toggle "Include unverified truck access" is on. The top level has `"include_unverified_default": false` (from `savings.include_unverified_truck_access_default`).

**`missed.json`** (Missed savings page)
```json
{"synthetic": true, "total_usd": 0.0, "fill_count": 0,
 "weekly": [{"week_start": "2025-11-03", "missed_savings_usd": 0.0, "fills": 0}],
 "rows": [{"txn_id": "FC0000123", "date": "2025-11-04", "truck_id": "TUC-03", "route_id": "TUC-R03",
           "actual_station_id": "STN-010", "alt_station_id": "STN-007", "gallons": 88.2,
           "actual_price_per_gal": 4.059, "alt_price_per_gal": 3.799,
           "alt_extra_miles": 1.8, "alt_extra_minutes": 5.0, "net_savings_usd": 17.42}]}
```
`rows` are sorted by `net_savings_usd`, largest first. Only fuel card fills are rows; yard fills never are. Since v1.6 the file also has `trucks_in_fleet`, `usd_per_truck_per_year` and `usd_per_truck_per_year_incl_unverified` (the overview rule). Since v1.7 it also has `card_gallons`, `cpg_per_card_gallon` and `cpg_per_card_gallon_incl_unverified` (total ÷ card gallons × 100, 2 decimals), which are the page's headline.

Truck access toggle: `total_usd`, `fill_count`, `weekly` and `rows` use confirmed alternatives only (the default). The same file also carries `total_usd_incl_unverified`, `fill_count_incl_unverified`, `weekly_incl_unverified` and `rows_incl_unverified`, computed with unverified stations allowed as alternatives, for when the toggle is on. Every row has `alt_truck_access`. `overview.json` `missed_savings_usd` always uses the confirmed-only total.

**`missed_paths.geojson`**: `LineString` features with properties `{"txn_id", "option": "actual" | "alternative"}` for the rows in `missed.json`. The file may be missing; the page then draws straight lines between stops.

**`partner_scout.json`** (Overview lead insight, v1.5; SPEC sections 5 and 7)
```json
{"synthetic": true, "scenario_label": "Synthetic scenario", "scope": "Tucson box, truck-confirmed stations that are not partners",
 "discount_steps_cpg": [0, 1, 2], "default_discount_cpg": 10, "best_station_id_at_default": "STN-004",
 "method": "One plain-English sentence on how shifted gallons and net savings are estimated.",
 "stations": [{"station_id": "STN-004", "display_name": "Pilot", "lat": 32.125678, "lon": -110.873704, "annual_savings_without_deal_usd": 274.60,
   "curve": [{"discount_cpg": 10, "fills_existing": 210, "gallons_existing": 15100.0, "fills_shifted": 40,
              "gallons_shifted": 3200.0, "gallons_per_month": 1525.0, "annual_net_savings_usd": 2150.40}]}]}
```
Rules:
- Candidates: stations inside `fetch.overpass.tucson_metro_bbox` with `truck_access` = `confirmed` and `is_partner` false. `stations` is ranked by `annual_net_savings_usd` at `default_discount_cpg`, largest first. Every station has one curve point per value in `discount_steps_cpg`.
- For a discount of X cents at station s, over every fill in the period (local and linehaul):
  - **Existing:** fills already at s save `G * X / 100` each.
  - **Shifted:** a fill at another station a shifts to s when s is feasible (missed-savings feasibility rules) and `gap_X = cost(a) - cost(s at posted price minus X) > savings.min_saving_threshold_usd`, using the SPEC section 6 cost formula with detours from `ref_detour`.
  - **Deal value per fill (v1.8, SPEC section 6, decision R1):** existing fills get `G * X / 100`. A shifted fill gets `min(gap_X - max(gap_0, 0), G * X / 100)`, where `gap_0` is the same gap at X = 0. The deal is never credited with savings that existed at posted prices, and never with more than the discount on the gallons it captures.
  - `annual_net_savings_usd` = the sum of deal values, scaled from the period length to 365 days. It is 0 at X = 0, and `net_savings_cpg_captured` is never above X. Pre-existing small gaps (below the threshold) are not reported. Large ones are the missed savings shown elsewhere and in `annual_savings_without_deal_usd`. `gallons_per_month` = (gallons_existing + gallons_shifted at X) scaled to an average month (365 / 12 days), because that is the volume to offer the station.
  - Each station also has `annual_savings_without_deal_usd`: the shifted savings at X = 0, annualized (what the fleet could already save there at posted prices).
  - v1.7: every curve point also has `net_savings_cpg_captured` = annual deal value ÷ annual captured gallons × 100, computed from the unrounded figures (not from the rounded `gallons_per_month`, which can push it a hair above X), in cents per captured gallon, 2 decimals (0 when no gallons). That is the scout's headline at the slider discount; dollars and per truck are secondary.
  - v1.6: every curve point also has `annual_net_savings_per_truck_usd` = `annual_net_savings_usd` ÷ `trucks_in_fleet`, and the top level has `trucks_in_fleet`. Fills are fuel card fills only; yard fills are never existing or shifted fills.
  - Never a raw price difference: shifted fills always include detour costs.

**`hub.json`** (Data hub page)
```json
{"synthetic": true,
 "sources": [{"name": "Fuel card export", "file": "fuel_card.csv", "rows": 0, "columns": ["TransactionID"], "sample": [{"TransactionID": "FC0000001"}]}],
 "clean_table": {"name": "fact_fuel_txn", "rows": 0, "columns": ["txn_id"], "sample": [{"txn_id": "FC0000001"}]},
 "dq": [{"check_name": "dedupe_swipes", "rows_in": 0, "rows_out": 0, "issue_count": 0, "notes": "..."}],
 "accuracy": [{"metric": "station_match_precision", "value": 0.987, "notes": "..."}]}
```
`sources` lists the fuel card, telematics and route plan feeds. Each `sample` has at most 5 rows.

## 8. CLI and stage entry points

| Command | What it runs |
|---|---|
| `uv run fleetfuel fetch [--refresh]` | `fleetfuel.fetch.run(cfg, refresh=False)`: EIA plus OSM into `data/raw/`, cache first |
| `uv run fleetfuel synth` | `fleetfuel.synth.run(cfg)`: every synth-owned file in section 4 |
| `uv run fleetfuel build` | in order: `ingest.run(cfg)`, `normalize.run(cfg)`, `routing.run(cfg)`, `analytics.run(cfg)`, `export.run(cfg)` |

- `build` reads only `data/raw/` and `data/cache/`. It never calls an API when the cache is complete, and needs no keys on a fresh clone.
- `ingest.run` and `normalize.run` accept optional keyword arguments `raw_dir: Path` and `db_path: Path` (defaults `config.RAW_DIR`, `config.DB_PATH`) so tests can point them at fixtures.
- Each `run()` prints one short progress line per step and raises a plain-English error when a required input is missing.

## 9. IDs and labels

| Thing | ID format | Display label |
|---|---|---|
| Phoenix distribution center | `PHX_DC` | Phoenix DC |
| Tucson yard | `TUC_YARD` | Tucson yard |
| Linehaul truck | `PHX-01`.. | PHX-01 |
| Local truck | `TUC-01`.. | TUC-01 |
| Local route | `TUC-R01`..`TUC-R10` | Route R01 |
| Linehaul route | `PHX-L01`..`PHX-L05` | Phoenix to Tucson linehaul, run 1 |
| Added station (chain locator) | `STN-A01`.. | the name from the locator |
| Customer | `CUST-0001`.. | Customer 001 |
| Station | `STN-001`.. | the OSM name, or the brand, or "Unnamed fuel station · STN-001" (`fleetfuel.stations.display_name`) |
| Telematics device | `GPS-#####` | not shown |
| Fuel card transaction | `FC#######` | shown on Missed savings |

## 10. Change log

| Date | Version | Change |
|---|---|---|
| 2026-11-16 | 1.0 | First version, before Wave 1. Extends SPEC section 4 with `fleet.csv`, `partner_program.csv`, `station_prices.csv` / `ref_station_price`, `dim_station.station_class`, the station match confidence fields, and the `dq_duplicate`, `dq_station_unmatched` and `eval_accuracy` tables. Uses station class price offsets instead of per-brand offsets. |
| 2026-11-16 | 1.1 | Added the EIA attribution rule to section 7 (CLAUDE.md rule 11). No format changes. |
| 2026-11-16 | 1.2 | openrouteservice base URL moved to `https://api.heigit.org` in `routing.ors_base_url`. ORS cache files are keyed by request body only, not the host. No format changes. |
| 2026-11-16 | 1.3 | Section 5: the OSM stations cache is stored filtered to truck-capable elements (data-fetcher report). Config adds `fetch.retry.*` and raises `fetch.overpass.dedupe_distance_m` to 100. No format changes. |
| 2026-11-16 | 1.4 | Cass's station decisions. Wider Tucson metro filter (cardlock, fleet access, diesel with truck evidence) with `truck_access` and `access_basis` on stations (4.2, dim_station, site stations.geojson). New 4.11: `config/station_overrides.yaml` keyed by `osm_ref`, the shared `fleetfuel.stations` module (owned by data-fetcher), and `station_override` rows in dq_log. Forbidden-term station names are dropped at fetch. `cardlock` station class. Truck access toggle fields in detours.json and missed.json. Unnamed station label. Later the same day: `truck_tagged` includes `hgv=designated`; the `load_stations` frame carries `merged_osm_refs`; `city` on site stations.geojson; 4.7 pins the `MERCH ######` code format and the unnamed-station merchant label. |
| 2026-11-17 | 1.5 | Cass's D0, D12/D13 and sanity-check answers. 4.2: every other Tucson-box diesel station is kept as `unverified` (`diesel_only`). 4.11: new `add` action for chain-locator confirmations (`STN-A01`.. or matched to an existing station, `access_basis` = `chain_locator`, source URL recorded). 4.9: five daily linehaul runs `PHX-L01`..`PHX-L05` (was one run a day for six tractors). Section 7: new `partner_scout.json` for the Tucson partnership scout, the Overview lead insight. Later: the scout's savings are what the deal creates (0 at X = 0), with `annual_savings_without_deal_usd` reported separately; 6.1 `an_result`; added cardlock sites classed `cardlock`; `savings.price_basis_weeks`. |
| 2026-11-17 | 1.6 | Cass's pre-review fixes. New 4.12 `yard_fuel.csv` and `fact_yard_fuel`: Phoenix DC on-site fills, never card transactions. `overview.json` splits card and yard gallons, plus per-truck-per-year missed savings. `missed.json` and `partner_scout.json` carry per-truck-per-year figures. `meta.json` gets `trucks_in_fleet` and `headline_assumptions` (from `site.headline_assumption_keys`). Break-even includes the wear term, the same components as net savings (SPEC section 6). `best_reason` says "with almost no extra driving" for tiny detours. |
| 2026-11-17 | 1.7 | Cass's review changes. Headlines as rates: `missed_savings_cpg_card`, `cpg_per_card_gallon` and `net_savings_cpg_captured`. `tucson_card_gallons_partner_share` replaces `partner_share_gallons`. The partner list is a stated rule (`prices.partner_set_bbox`), not a random draw. `derived.` headline assumptions (Tucson route miles a day). Two EIA captions: the plain source line only for actual EIA numbers, the "benchmark source" line for synthetic prices. |
| 2026-11-17 | 1.8 | Cass's R1 answer: scout deal value per fill is capped at the discount on the gallons it captures, and pre-existing gaps are never deal value. Partner set limited to named truck stops (`prices.partner_set_named_only`). The derived route length note reads "from the synthetic route plans, set by generator assumptions". |
| 2026-11-17 | 1.9 | Time-cost upgrade (SPEC section 6): peak multiplier from Tucson's published Travel Time Index, an optional Planning Time Index buffer, max detour minutes, schedule-risk charge and overtime. New detour option fields, `break_even_cpg_peak` and `worked_example` in detours.json. |
| 2026-11-18 | 1.17 | Section 17: value badges hold short values only (long values and derived notes move to the description, no "derived from the data" badge); new `guide.html` "Start here" page, first in the sidebar, linked from every banner and the Overview. |
| 2026-11-18 | 1.16 | Section 16: at least 3 candidate businesses per customer point. |
| 2026-11-17 | 1.15 | Section 15: the customer sample, order and offsets are unseeded and stored nowhere (the v1.14 seeded move was reversible); Retry-After cap; retries count toward the daily limit; refused retries cached; checker hardening. |
| 2026-11-17 | 1.14 | Section 14: seeded 300 to 600 m offset on every customer point (cache and CSV hold moved points only), openrouteservice snapping fallback, `refresh_cache` with pruning, history and coordinate checks in `scripts/`. |
| 2026-11-17 | 1.13 | Section 13: `why` and `why_line` on missed rows; "Lower-cost option" replaces "Cheaper alternative" on the Missed savings page, with a Why column. |
| 2026-11-17 | 1.12 | Section 12: `build_version` in meta.json, cache-busting `?v=` on every CSS, JS and data URL (export stamps the HTML); Test route options grouped under one "Costed at" heading per stop time; final hand-checked fills in `docs/hand-checks.md`. |
| 2026-11-17 | 1.11 | Cass's site review fixes (section 11): schedule risk is a ranking penalty only (never in missed savings, break-even or the scout); TTI 1.17 (Texas A&M) with FHWA 1.07 as cross-check; 15-minute detour cap; typical fill 72 gallons; one cost per Test route option at the planned time of its stop (`*_peak` fields and `break_even_cpg_peak` removed); `default_route_id`, `rush_hour_note`, `ranking_note`; `detour_paths.geojson` replaces `missed_paths.geojson`; missed rows carry `price_gap_usd`, `actual_detour_cost_usd`, `alt_detour_cost_usd` and insertion points; `stops_json` gains `planned_arrival`; owner credit exception (CLAUDE.md rule 2, contract rule 7). After merge the orchestrator tightened the best-stop rule (11.3: only options that save real dollars can win). |
| 2026-11-17 | 1.10 | Final assumption pass: the overtime term is removed and replaced by an hours-of-service check (`hos_check` in overview.json, `hos.*` config). Phoenix yard discount 20 cents. Cost assumptions carry their source (name, year, URL) in the config note where a public, citable source exists. The yard bulk range and the fuel-card network discounts say plainly why none is cited (an owner's figure, or no company names allowed). |

## 11. v1.11 changes (Cass's site review, Nov 17). Where this section and an older one disagree, this section wins.

### 11.1 Cost rules (analytics-builder)
- **Schedule risk is a ranking penalty only.** `cost_parts` and every dollar figure (missed savings, the scout, break-even, the worked example, every Test route dollar) exclude it. `time_cost.schedule_risk_usd_per_minute` is used only to rank Test route options (11.3). Remove the risk term from the cost total; a separate function computes the penalty.
- `time_cost.travel_time_index` is now 1.17 and `time_cost.max_detour_minutes` 15 (config). Feasibility and `over_max_detour` judge **effective** minutes (`extra_minutes * m(t)`) against the cap. The cap applies to alternatives and recommendations, never to pricing the station a driver actually used.
- `savings.typical_fill_gal` is now 72.
- **Time of a stop** (Test route and break-even): an option inserted after stop `N` happens at `planned_arrival(N)` plus `routes.minutes_per_stop` when stop `N` is a customer (plus 0 at a yard). That time is treated as a weekday, so `m(t)` = the peak multiplier inside `time_cost.peak_windows`, else 1. Missed savings and the scout keep using each fill's own timestamp.

### 11.2 `dim_route.stops_json` (pipeline-builder)
Every stop object gains `"planned_arrival": "HH:MM"`, the `PlannedArrival` of that `RouteID` and `StopSeq` on the first `PlanDate` in `route_plans.csv`. Nothing else in `dim_route` changes. Analytics raises a plain-English error naming the route if a stop lacks it.

### 11.3 `detours.json` (analytics-builder writes, site-builder reads)
Top level, new:
- `default_route_id`: the TUC route whose `best_station_id` option has the largest `net_savings_vs_baseline_usd` (a route whose best is its baseline counts as 0; ties broken by route_id).
- `rush_hour_note`: built from config, for example "Rush hour, weekdays 6 to 9 am and 4 to 7 pm: detour minutes count 1.17 times (Tucson Travel Time Index, Texas A&M 2025 Urban Mobility Report)."
- `ranking_note`: exactly "Stops before the last delivery are ranked lower to protect the delivery schedule."
- `price_period_start`, `price_period_end`: dates of the price basis window.

Each option now has exactly these fields: `station_id, truck_access, insert_after_stop, stop_time_local ("HH:MM"), is_peak (bool), multiplier, extra_miles, extra_minutes (openrouteservice minutes), extra_minutes_effective (extra_minutes * multiplier, 1 decimal), price_per_gal, fuel_cost_usd, detour_cost_usd (fuel burned + driver time + wear, no schedule risk), total_cost_usd, net_savings_vs_baseline_usd, before_last_delivery (bool), ranking_penalty_usd (effective minutes * schedule_risk_usd_per_minute when before_last_delivery, else 0), over_max_detour (effective minutes > max_detour_minutes)`.
Removed: `extra_minutes_peak`, `detour_cost_peak_usd`, `total_cost_peak_usd`, `net_savings_vs_baseline_peak_usd`, `over_max_detour_peak`.
- The baseline option is priced the same way, at its own stop time.
- **Best stop ranking:** among allowed options (confirmed only, or with unverified, and not `over_max_detour`), score = `net_savings_vs_baseline_usd - (ranking_penalty_usd - baseline.ranking_penalty_usd)`. Only options with `net_savings_vs_baseline_usd` above 0 (real dollars) can win; among them the best is the highest score, and if that score is not above 0, the best is the baseline. So the penalty can reorder or rule out money-saving stops, but never makes a dearer stop the recommendation (orchestrator fix after merge). `best_reason` quotes the dollar `net_savings_vs_baseline_usd` (never the score), the fill size and the detour.
- `worked_example`: built from `time_cost.worked_example_*` (now 12 minutes, 4 miles, 07:00) and `savings.typical_fill_gal` (72). Remove `schedule_risk_usd`. Add `price_period_start`, `price_period_end`, `price_basis` (text). The `sentence` states the fill size and the price period, for example "For a 72-gallon fill at $5.080 a gallon (median Tucson posted price, Oct 4 to Oct 31, 2026), a 12-minute detour at 7 am costs $10.24 ($8.89 driver time, $3.39 fuel, $1.00 wear), so the station needs to be at least 14.2 cents a gallon cheaper to pay off." (numbers illustrative only).

### 11.4 `stations.geojson` (analytics-builder)
`break_even_cpg` is computed at the stop time of the median Tucson route's insertion (11.1), with no schedule risk. New: `break_even_route_id`, `break_even_is_peak` (bool). Removed: `break_even_cpg_peak`.

### 11.5 `missed.json` rows (analytics-builder)
Every row (both `rows` and `rows_incl_unverified`) also has: `actual_extra_miles`, `actual_extra_minutes`, `actual_insert_after_stop`, `alt_insert_after_stop` (now public), `is_peak`, `multiplier`, `price_gap_usd` (= (actual_price_per_gal - alt_price_per_gal) * gallons), `actual_detour_cost_usd`, `alt_detour_cost_usd`. Identity, checked by a test on unrounded values and to within 1 cent after rounding: `net_savings_usd = price_gap_usd + actual_detour_cost_usd - alt_detour_cost_usd`. Detour costs are fuel burned + driver time + wear at the fill's own time; no schedule risk.

### 11.6 `detour_paths.geojson` (routing-engineer provides the function, analytics-builder's export writes the file)
Replaces `missed_paths.geojson` (export deletes the old file; `export.max_missed_paths` is gone, `export.max_detour_paths` caps the count). A `FeatureCollection` with `"synthetic": true` and one `LineString` per `ref_detour` row, properties `{"route_id", "station_id", "insert_after_stop"}`. The line is the road path from stop `insert_after_stop` to the station to the next stop, from openrouteservice directions, coordinates `[lon, lat]` with 5 decimals. Rows whose path is not available are left out (the site falls back to a straight line and says so).
- **routing-engineer:** new public `fleetfuel.routing.detour_leg_path(cfg, route_id, station_id, insert_after_stop, *, db_path=None, raw_dir=None, cache_dir=None, client=None) -> dict` (a GeoJSON LineString): one directions call with three waypoints (stop N, station, stop N+1), cached under `data/cache/ors/` like every other call. Raises `KeyError` for an unknown route, station or stop. Never calls the network on a cache hit; with no key and a cache miss it raises a plain-English error. Also warm the cache for every `ref_detour` row once (within free-plan limits, `routing.ors_pause_s` between calls) and commit the cache files.

### 11.7 `partner_scout.json` (analytics-builder)
Same shape. `method` describes only what is counted; schedule risk is not part of any gap. Values change because the cost rules changed.

### 11.8 `meta.json`
`headline_assumptions` follows the new `site.headline_assumption_keys` (schedule risk removed, `savings.typical_fill_gal` added).

### 11.9 Site (site-builder)
- **Test route:** open on `detours.default_route_id` (fall back to the first route). Each option shows one cost line (its `net_savings_vs_baseline_usd`, at its `stop_time_local`); no off-peak and rush pairs anywhere. Show `rush_hour_note` once and `ranking_note` once (replacing the per-option "schedule-risk charge" chips). Draw the selected detour from `detour_paths.geojson` (match `route_id`, `station_id`, `insert_after_stop`); a straight line plus a short note only when no path exists. The legend says "Detour (fuel stop)" without "straight line". The worked example shows its sentence (it already states fill and price period).
- **Missed savings:** remove the "Per extra mile" column. The comparison shows the station used with its real `actual_extra_miles` and `actual_extra_minutes` (never "0 extra miles"), the alternative with its detour, and the saving as its parts, for example "Price gap x gallons $13.27 + detour avoided at the station used $19.93 - detour to the alternative $0.53 = net saving $32.67". Map: base route (routes.geojson) plus both detour legs from `detour_paths.geojson`.
- **Station map:** one break-even number (`break_even_cpg`), plus the short rush-hour note when `break_even_is_peak`; never off-peak and rush side by side. The map page may load `detours.json` for the note.
- **Overview:** scout unit "saved per gallon moved to this station" (replaces "per gallon captured" everywhere, including the hero label and the sentence); drop the "Already available at posted prices" line from the card; replace the method paragraph (`scout-foot`) with a "How this is calculated" link to `assumptions.html#how`.
- **Assumptions page:** new section `id="how"`, "How this is calculated": the net-savings rule, the missed-savings breakdown (with the top row of `missed.json` as a live example), the scout `method` and the top scout station's "Already available at posted prices" figure, `rush_hour_note` and `ranking_note`.
- **Every page's sidebar footer:** "Built by Cass Bowes" with a GitHub link to `https://github.com/bowesie92` (no LinkedIn).

## 12. v1.12 changes (Cass's last small fixes, Nov 17). Where this section and an older one disagree, this section wins.

### 12.1 Build version and cache busting (analytics-builder stamps, site-builder reads)
- **Version:** at the end of `export.run`, compute `build_version` = the first 12 hex characters of SHA-256 over every file in `site/data/` except `meta.json`, then every file in `site/css/` and `site/js/`, each fed as its posix path relative to `site/` plus its bytes with line endings normalised to LF, in sorted path order. Same inputs give the same version; any change to data, CSS or JS gives a new one.
- `meta.json` gains `"build_version"`.
- **Stamping:** export then rewrites every `site/*.html`. The only edits allowed:
  - Every local stylesheet and script URL (`href="css/....css"`, `src="js/....js"`) gets `?v=<build_version>`, replacing any existing `?v=` value.
  - `<meta name="build-version" content="<build_version>">` sits right after `<meta charset="utf-8">`, inserted once and replaced on later runs.
  - Nothing else in the HTML changes. Stamping is idempotent: a second run with the same version leaves the files byte-identical.
  - Export may edit `site/*.html` only for this.
- **Data URLs:** `common.js` appends `?v=<content of the build-version meta>` to every `data/` URL it fetches (`FFH.loadData`, `FFH.loadOptional` and any other data fetch). With no meta tag, URLs stay as they are. External CDN URLs are never changed.

### 12.2 Test route option list (site-builder)
Options are sorted by `stop_time_local` (then by their current order) and grouped. Each distinct time gets one heading above its group, "Costed at 7:31 am" (with "(rush hour)" when `is_peak`). The per-option "Costed at" line is removed. The selected stop's card on the stop list may still name its time.

### 12.3 Hand-checked fills (analytics-builder)
`docs/hand-checks.md` (orchestrator) holds the three final fills FC1774473, FC0733626 and FC9591385, with full arithmetic. `tests/test_analytics_handchecked.py` checks exactly these:
- Inputs match the committed raw files: `fuel_card.csv` Unit, date, time, Gallons and PPG; `station_prices.csv` posted price of the alternative that day.
- Detour miles and minutes match the rounded fields of the same rows in `site/data/missed.json`.
- The shared cost function on the full-precision inputs gives each documented part and net to the cent.
- `site/data/missed.json` shows the documented `net_savings_usd`, `price_gap_usd`, `actual_detour_cost_usd` and `alt_detour_cost_usd`.

The made-up mini-hub fills F1 to F3 move unchanged to `tests/test_analytics_fixture_fills.py`, with a docstring saying they are fixture fills, not the hand checks.

## 13. v1.13 changes (Cass, Nov 17): why the lower-cost option wins

### 13.1 `missed.json` rows (analytics-builder)
Every row (in `rows` and `rows_incl_unverified`) gains:
- `why`: exactly one of `"lower price"`, `"shorter detour"`, `"both"`. It is computed on unrounded values:
  - "lower price" means `alt_price_per_gal < actual_price_per_gal`.
  - "shorter detour" means the alternative's detour cost is below the used station's.
  - `"both"` when both hold. Since every row saves more than the threshold, at least one always holds.
- `why_line`: one plain-English sentence for the comparison panel, built from the same facts. Miles saved = each side rounded to 1 decimal first (as the panel prints them), then subtracted, written with the trailing ".0" dropped, so 5.0 becomes "5" (QA fix: the line always matches the miles shown above it).
  - shorter detour only, alternative's pump price higher: "Higher pump price, but saves a 5-mile detour."
  - shorter detour only, same pump price: "Same pump price, and saves a 5-mile detour."
  - lower price only: "Lower pump price, worth the extra 0.3-mile detour." When the alternative is not more miles but takes more minutes, say "worth the slightly longer detour".
  - both: "Lower pump price and saves an 8.2-mile detour."
  - When a shorter-detour case saves under 0.1 mile (the saving is in minutes), "saves a N-mile detour" becomes "a shorter detour": "Lower pump price and a shorter detour.", "Higher pump price, but a shorter detour."
  - Use "an" before 8, 11 and 18 ("an 8.2-mile", "an 11-mile").

### 13.2 Site (site-builder)
- The Missed savings column "Cheaper alternative" is renamed "Lower-cost option", everywhere it appears on that page (table header, column menu, map marker label, comparison panel label, CSV header label if any).
- A new short "Why" column after it shows `why`.
- The comparison panel shows `why_line` as one line, directly under the two station lines.
- The Assumptions "How this is calculated" example uses the same wording ("lower-cost option").

## 14. v1.14 changes (Cass, Nov 17): customer points moved off real businesses

### 14.1 Customer point offset (data-fetcher)
- **What:** every stand-in customer point moves a seeded random distance between `fetch.customers.offset_min_m` (300) and `fetch.customers.offset_max_m` (600), in a random direction, so no stop sits on a real business. CUST IDs stay exactly as they are: `CUST-0014` is the moved version of the same original point.
- **Seeding and draws:** seeded from the top-level `seed`, drawn in CUST order. A draw is redrawn from the same random stream when either:
  - the rounded point (`fetch.customers.coord_decimals`) lands within `fetch.customers.offset_clear_of_any_original_m` (50) of ANY original point; or
  - rounding pushes its distance outside the 300 to 600 m range.
- **Where it happens:** in the sanitize step, before anything is written. `data/cache/osm/customers_points.json` and `data/raw/customer_points.csv` hold moved points only; no file ever stores an original coordinate. Reading the cache never offsets a second time: the cache records that it holds moved points and with which settings. A rerun of `fleetfuel fetch` from cache gives byte-identical files.
- **One-time migration:** transform the existing cache file and CSV in place. No live Overpass call is needed or allowed.
- **Check:** `scripts/check_customer_offsets.py --originals-rev 9406634` (orchestrator) must pass after the full rebuild.

### 14.2 Routing refresh and pruning (routing-engineer)
- **Snapping fallback:** if openrouteservice answers that it cannot find a routable point (a moved point may sit off the road network), retry that one request once with `radiuses` of `routing.snap_retry_radius_m` (1000) for every waypoint, where the endpoint supports it.
  - The retry is cached under its own request body.
  - Reading tries the normal body's cache, then the retry body's cache, and only then the network.
  - If an endpoint has no radius option (check the matrix API), report it rather than guess.
- **`fleetfuel.routing.refresh_cache(cfg, *, prune=False, ...) -> dict`:** in one process, it runs the routing step (routes and ref_detour) and warms every detour leg, tracking every `data/cache/ors/` file read or written.
  - With `prune=True` it then deletes every `data/cache/ors/` file not used in that run.
  - It returns counts: live calls, cache hits, failed requests and files pruned.
  - It respects the free-plan limits and `routing.ors_pause_s`, and is never called by `fleetfuel build`.
  - The orchestrator runs it live after the resynth.

### 14.3 After merge (orchestrator)
`uv run fleetfuel synth`, then `refresh_cache(prune=True)` live, then `uv run fleetfuel build`, then the coordinate check, the full-history scans, new hand-check fills in `docs/hand-checks.md`, and refreshed site test fixtures (the v111 routes and detour-path fixtures were copied from real data).

## 15. v1.15 changes (QA must-fix, Nov 17): the move must not be reversible

qa-reviewer showed that the v1.14 move could be undone. The sample and the offsets came from the public `seed`, so replaying the stream mapped every moved point back to within 5 m of its original. Section 14.1's seeding rule is replaced by this one.

### 15.1 Customer points (data-fetcher)
- **Unseeded draws:** the choice of which originals become customers, their CUST order, and every offset come from an unseeded source (`secrets.SystemRandom`).
  - They are drawn once. No seed, offset, bearing or index is stored anywhere.
  - The cache of moved points is the only source of truth after that. Reading it never redraws, and a build from cache is byte-identical.
  - `fetch --refresh` draws afresh, so the points change (documented).
- **CUST IDs** stay `CUST-0001` to `CUST-0300`. Which business each one started from is now random and recorded nowhere.
- **Rules:**
  - each point is 300 to 600 m from its own original;
  - after rounding, each point is at least `offset_clear_of_any_original_m` from every original;
  - the cache records only the settings (min, max, clear distance) and `"randomness": "unseeded, drawn once"`.
- **One-time migration:**
  - The originals come from git history, `git show 9406634:data/cache/osm/customers_points.json` (995 points), held in memory only. Nothing original is written to the tree.
  - Redraw, then replace the cache and CSV.

### 15.2 Routing (routing-engineer)
- **Long waits:** a Retry-After longer than `routing.max_retry_after_s` (120) is not slept on. It means the daily quota or a long block, so the request fails with a plain error and counts as failed.
- **Daily counter:** every POST attempt, retries included, counts against the daily limit.
- **Refused retries:** a refusal of the wider-radius retry is cached, so a rerun neither spends a call on it nor blocks pruning forever. Such a leg counts as "unroutable", not "failed", and is reported.

### 15.3 Orchestrator
- **Hard rule 12:** names the station-name screening in `fetch` as a third reader of the terms list, and narrows "scripts that import it" to committed code.
- **`check_repo_rules.py`:** fails in `--history` or `--repo` mode when the terms list is missing.
- **`check_customer_offsets.py`:** checks against all 995 old cache points, not just the 300 sampled. Since the CUST-to-original mapping is now unknown, "moved" means each point is 300 to 600 m from at least one original and at least 50 m from all.
- **Publishing plan:** repoint every other clone before the new repo exists, add a push guard, and confirm the commit before archiving.

### 15.4 Every customer point must be reachable by truck (Nov 17, found in the live rebuild)
The first unseeded draw put a point inside an air base, where openrouteservice found no truck road within 1,000 m, so the route could not be built. From now on a draw is accepted only if every point can be reached.
- **routing-engineer:** a public `fleetfuel.routing.reachable_points(cfg, points, *, cache_dir=None, client=None) -> list[bool]`.
  - It uses the openrouteservice snap service for `driving-hgv` (check the docs for the endpoint and limits), batching as many points per call as allowed, with radius `routing.reachable_radius_m` (new, 350, the directions default).
  - Responses are cached like every other call, the shared limiter applies, and calls count toward a daily limit.
  - It returns True for each point that snaps to a truck road within the radius.
- **data-fetcher:** the one-time unseeded draw (15.1) loops.
  - After drawing, it asks `reachable_points`, then redraws only the unreachable points under the same rules, still in memory with the originals. It repeats until all 300 are reachable, or stops with a clear error after `fetch.customers.max_reach_rounds` (new, 10).
  - Nothing about the draw is stored, as before.

## 16. v1.16 changes (decision P1, Cass, Nov 18): at least 3 candidate businesses per point

A moved point hides its business only when several originals could be the one it came from. With one candidate, anyone rerunning the public OpenStreetMap query can draw the 300 to 600 m ring and name the business.

### 16.1 Customer points (data-fetcher)
- **New setting** `fetch.customers.min_candidate_originals: 3`. A draw is accepted only if at least this many originals (counting each place once) lie 300 to 600 m from the rounded point, on top of the 15.1 rules and the 15.4 reachability check.
- **Cache format:** `settings` gains `"min_candidates": 3`, so it reads `{"min_m": 300.0, "max_m": 600.0, "clear_m": 50.0, "min_candidates": 3}`. A cache with other settings is refused, as before.
- **Fresh draws** (`fetch --refresh`): `osm.choose_and_move` takes originals in an unseeded random order and skips any too far from other businesses to pass the rule, until 300 are placed.
- **One-time fix:** `scripts/redraw_thin_customers.py --originals-rev 9406634` redraws only the points that fail the rule (38 of 300: 16 with one candidate, 22 with two). Each is drawn around one of its current candidates picked at random, or another random original when none leaves room. The originals come from git history and stay in memory. CUST IDs stay, and the other 262 points are untouched.

### 16.2 Orchestrator
- `check_customer_offsets.py` also fails when any point has fewer than `min_candidate_originals` candidates, and reports the fewest seen. It counts each original place once (the old CSV points are also in the old cache).
- The tree must hold no coordinate within 5 m of the 38 replaced positions either (they were the identifiable ones), checked once against the commit before the redraw.
- **Moving GPS pings:** a telematics row with `speed_mph` above 0 is a point on a road, not a place. When one passes within the 5 m radius of an original (one did after this resynth: 3.6 m, at 21 mph, 419 m from any customer stop), the check reports it on its own line instead of failing. This applies only to `data/raw/telematics.csv` rows at `fetch.customers.road_ping_min_mph` (5) or more; more than `road_ping_max_exempt` (3) such pings in one file, or one within the clear distance of a customer stop, still fails. Slower and parked pings always fail.
- `tests/fixtures/site/v111` (routes and detour paths) is regenerated from the new data, because it held the old stop positions.

## 17. v1.17 changes (Cass, Nov 18): short value badges and a "Start here" page

Site only (site-builder). No data format changes.

### 17.1 Value badges
- A value badge (`.chip` holding an assumption value) holds only a short value: a number and its unit, at most 25 characters. A longer value (a sentence, a source name, a URL, a list) is never put in a badge; it goes in the description text under the label, before the note.
- A derived entry's note (for example "synthetic-route assumption: openrouteservice road miles over the synthetic route plans") goes in the description text, not in parentheses after the badge. This replaces the "value, unit, then the note in parentheses" wording in section 7.
- The "derived from the data" badge is removed everywhere (the Overview and Missed savings panel and the Assumptions page). The description text says where the number comes from instead.
- Badges may wrap: no `white-space: nowrap` on a value badge, and a badge never grows past its column (`max-width: 100%`, long words break).
- On the Assumptions page table, the Value cell holds only a short value; a long value moves into the "What it means" cell, before the note.
- No horizontal overflow on any page at 1280, 1920 and 2560 px wide, or at 375 px (mobile). Tables that are wider than the screen scroll inside their own card, never the page.

### 17.2 "Start here" page
- New page `site/guide.html`, title "Start here: a 5-minute tour", with the same shell as every other page (SYNTHETIC DATA strip with the Assumptions link, sidebar, "Data as of" card, owner credit footer).
- **Sidebar order:** Start here, Overview, Station map, Test route, Missed savings, Data hub. The mobile tab bar keeps its five tabs; the guide is reached from the banner link and the Overview link.
- **Banner:** every page's top strip gains a "New here? Start here" link to `guide.html`.
- **Overview:** a "New here? Start here" link at the top of the page, above the scout card.
- Content is Cass's text (Nov 18) verbatim: what this is, the scenario, five numbered steps each linked to its page (`index.html`, `map.html`, `route.html`, `missed.html`, `hub.html`), how to read the numbers (linking `assumptions.html`), and what would be different with real data. No charts, so no EIA caption.

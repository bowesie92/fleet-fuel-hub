---
name: routing-engineer
description: Wave 2 builder. openrouteservice driving-hgv routing, base route geometry and the precomputed ref_detour table for every route x station, within free-plan limits and fully cached. Owns src/fleetfuel/routing/ and data/cache/ors/.
model: sonnet
isolation: worktree
disallowedTools: Agent
---

You are the **routing-engineer** for fleet-fuel-hub (Wave 2). You run in your own git worktree on your own branch.

## Before you start
1. Read `CLAUDE.md`, `SPEC.md` (sections 2, 3, 4 and 6) and `docs/contracts.md` in full. The contract is the interface. Build to it exactly.
2. Run `uv sync` in your worktree.

## Folders and files you own (edit nothing else)
- `src/fleetfuel/routing/`
- `data/cache/ors/`
- `tests/test_routing_*.py`, `tests/fixtures/routing/`

If you need a change anywhere else (synth, pipeline, config, contract), stop that piece and report it.

## Tasks
1. Read the routes from `dim_route` in `data/hub.duckdb` (or from `data/raw/route_plans.csv` plus contract 4.9), and the stations from `dim_station`.
2. **Base route geometry**: one openrouteservice `driving-hgv` directions call per route. Update `dim_route.base_miles`, `base_minutes` and `geometry_geojson`.
3. **ref_detour** (contract 6): for every route x candidate station, find the cheapest insertion position (`insert_after_stop`) and its extra miles and extra minutes versus the base route, using **matrix** calls rather than many directions calls. Insertion cost = d(stop_i, s) + d(s, stop_i+1) - d(stop_i, stop_i+1). Stay under the matrix limit of 3,500 cells per call by choosing sources and destinations carefully.
4. Expose a small function that returns cached road geometry for a route, so synth can use real road paths for telematics later. Report the signature.
5. Wire `fleetfuel.routing.run(cfg)` into the build (contract 8).

## Rules
- Key: `fleetfuel.config.load_env()` then `os.environ["ORS_API_KEY"]`. Never read `.env` directly, never print or cache the key.
- Base URL: read `routing.ors_base_url` (now `https://api.heigit.org`, because api.openrouteservice.org is being deprecated). Never hardcode a host. Before the first live call, confirm the directions and matrix path layout on the new host from the official openrouteservice docs, and build URLs as base URL plus path so a host change is a one-line config edit. Leave the base URL out of the cache key, so cached responses survive a host change.
- Free plan limits (CLAUDE.md rule 9): about 2,000 directions and 500 matrix requests a day, 40 a minute. Pause `routing.ors_pause_s` between calls. Count calls and print the total.
- Cache every response under `data/cache/ors/`, keyed by a hash of the request body with no key in it. Read the cache first. A fresh clone with no key must build entirely from cache. On a cache miss with no key, fail with a plain message.
- Read every parameter from `config/assumptions.yaml` (`routing.*`). Report any missing keys.
- No magic numbers, no new dependencies, no em dashes.

## Done when
- `ref_detour` has a row for every route x candidate station, and `dim_route` has real geometry.
- A second build makes zero API calls.
- Tests run offline from fixtures or the cache.
- `uv run pytest` passes.

## Report (under 25 lines)
Branch name; what you built; API calls used (directions, matrix); ref_detour row count; test results; open issues. Do not merge or push.

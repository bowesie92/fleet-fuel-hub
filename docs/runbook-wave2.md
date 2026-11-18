# Wave 2 runbook (for the orchestrator)

Wave 1 is on `main` (6178df2). **Wave 2 starts only after Cass answers D0** in
`docs/decisions-pending.md` (fewer than 5 confirmed Tucson stations). If his answer
changes the station set, do step W0 first.

**W0. Station set (only if D0 changes it).**
- If Cass picks hand-confirmed stations, add a `confirm_truck_access` action to contract 4.11 and to `config/station_overrides.yaml`, then send data-fetcher to implement it in `fleetfuel.stations`.
- If he picks "diesel stations as unverified", change the 4.2 rules and send data-fetcher to refetch.
- Then regenerate `data/raw` (`uv run fleetfuel synth`), run ingest and normalize, check `eval_accuracy`, run qa-reviewer, and push to `main`. Revisit D12 and D13 with the new counts.

**W1. Launch in parallel** (one message, `isolation: worktree`):
- **routing-engineer**: contracts 1, 2, 3, 4.9, 4.11, 5, 6 (`dim_route`, `ref_detour`), 8.
  - Base URL `routing.ors_base_url` (https://api.heigit.org). Confirm the path layout from the docs first.
  - Key from `ORS_API_KEY`; free-plan limits; cache everything.
  - Candidate stations come from `fleetfuel.stations.load_stations()`, including unverified ones, so the toggle has data.
- **analytics-builder**: contracts 1, 2, 3, 4.11, 6, 7 (all of it, including the v1.4 toggle fields and `city`), 8, 9.
  - Build against a fixture `ref_detour` in `tests/fixtures/analytics/` until routing merges.
  - Compute confirmed-only and incl-unverified versions of `detours.json` and `missed.json`.
  - Use `fleetfuel.stations.display_name`. Export owns `site/data/` from now on, `meta.json` included.

**W2. Merge** routing first, then analytics. Run `uv run pytest` after each merge. Then run `uv run fleetfuel build` end to end on a clean checkout with no keys: a second build must make zero API calls.

**W3. Site on real data.** Send site-builder to run the six pages on the exported files (not placeholders) at desktop and 375px, and fix anything the real data exposes (empty states, long names, nulls). Pick 3 sample fills and hand-check the missed savings math (SPEC section 10).

**W4. qa-reviewer**, including STYLE.md section 6 and the SPEC section 6 math. Loop until no MUST-FIX.

**W5. Wrap up.** Build log, `scripts/check_repo_rules.py` (also on the commit message), push to `main`, report to Cass.

## Prompt for Cass to paste next session

```
You are the orchestrator. Read CLAUDE.md, SPEC.md, docs/contracts.md, docs/decisions-pending.md
and docs/runbook-wave2.md. My answer to D0 is: <option number and any stations to confirm>.
My answers to the other open decisions: <D-numbers and answers, or "use your defaults">.
Apply them, then run Wave 2 per the runbook (W0 if needed, then W1 to W5).
```

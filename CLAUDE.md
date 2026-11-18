# CLAUDE.md: fleet-fuel-hub

Read SPEC.md and docs/contracts.md (once it exists) before any task. SPEC.md is the source of truth. If a request conflicts with SPEC.md, stop and ask.

## About the owner

Cass is an MS in AI in Business student with an accounting and audit background. He knows Python and pandas, SQL, n8n and Docker, and he wants to understand every part of this repo well enough to explain it in an interview. So:

- Explain what you are about to build and why, in plain English, before writing code for a new module.
- Keep functions small and clearly named. Give each a short docstring that says what it does in business terms.
- After each milestone or agent wave, add a 5 to 10 line entry to `docs/build-log.md`: what was built, key decisions, and what Cass should understand before moving on.

## Hard rules

1. **No real company data, ever.** Only public data (EIA, OpenStreetMap, openrouteservice) and synthetic data from `src/fleetfuel/synth/`.
2. **No real company names, people's names, logos or brand colors** anywhere in code, data, site, docs or commit messages. Use "the distributor", "Phoenix DC" and "Tucson yard." **Exception:** the project owner's own name, Cass Bowes, may appear as the author credit ("Built by Cass Bowes") in the README, the site sidebar footer and the LICENSE, with links to his own profiles. It never appears in synthetic data, and no other person's name is allowed. The file `.local/forbidden_terms.txt` (gitignored, one term per line) lists words that must never appear in the repo. Check against it before every commit. Never copy its contents anywhere.
3. **Customer stops are anonymized** ("Customer 014"). Never store or show a real business name for a stop.
4. **Every site page shows a SYNTHETIC DATA banner** and links to the assumptions.
5. **Every assumption lives in `config/assumptions.yaml`.** No magic numbers in code.
6. **Savings are always net of detour costs** (SPEC.md section 6). Never report a raw price difference as savings.
7. **Never commit secrets.** Keys come from `.env` (gitignored). Use `.env.example` for key names.
8. **Cache every external API response** under `data/cache/` and read the cache first. A fresh clone must build with no API keys.
9. **Respect API limits.** openrouteservice free plan: about 2,000 directions and 500 matrix requests a day, 40 a minute. Prefer one matrix call to many directions calls, and pause between calls. The base URL is `https://api.heigit.org` (api.openrouteservice.org is being deprecated) and lives only in `routing.ors_base_url` in `config/assumptions.yaml`; the key comes from `ORS_API_KEY` in `.env`. No openrouteservice calls before Wave 2. Overpass: one query at a time, with a polite User-Agent.
10. Do not use em dashes in any text, docs or UI copy.
11. **Credit EIA accurately, never imply endorsement.** Two captions, never mixed up:
    - A chart that plots **actual EIA numbers** (the EIA weekly series itself) carries "Source: U.S. Energy Information Administration (EIA)".
    - Anything that shows **synthetic prices** (station posted or paid prices, yard prices, prices in tables, tiles and samples, and anything derived from them) carries "Synthetic prices built from the EIA weekly diesel average (benchmark source: U.S. Energy Information Administration)", never the plain source line.
    - A chart showing both gets both lines.
    - No EIA logo, and no wording that suggests EIA reviewed, approved or endorses this project or its numbers.

12. **Never read `.env` or `.local/` by any means**: not with the Read tool, shell commands, or scripts written for a task (Cass, Nov 17). Only three pieces of committed code may read them, and none of them prints what it read:
    - `scripts/check_repo_rules.py` loads `.local/forbidden_terms.txt` and prints counts and paths only, never a term. The committed tests and scripts in this repo that call it count as part of it; a script written on the spot for a task does not.
    - The station fetch (`fleetfuel.fetch.osm.load_forbidden_terms`, through that same loader) drops stations whose name matches a forbidden term, keeping the terms in memory only.
    - The app's config loader (`fleetfuel.config.load_env`) reads API keys from `.env` at run time for live API calls.
    Secret scans use patterns only (`scripts/check_repo_rules.py --secrets`), never the real key values. Full-history scans use `--history`.

## Orchestration (how work is split across agents)

The main session is the **orchestrator**. It plans, writes contracts, dispatches subagents, merges their work and reports to Cass. It writes little feature code itself.

- **Contracts first.** Before any parallel wave, the orchestrator writes or updates `docs/contracts.md`, covering file formats, column names and types, the DuckDB schema, site JSON formats, and which agent owns which folders. It commits this to `main` before launching agents, because worktrees branch from `main`.
- **Agents live in `.claude/agents/`.** Each builder runs in its own git worktree (`isolation: worktree`) and only edits folders it owns. If it needs something outside its folders, or a contract change, it stops and reports instead of editing.
- **Launch a wave in parallel.** Use one message with several Agent calls. Give each agent its brief, the relevant contract sections, and its "done when" criteria.
- **Builders commit in their worktree** with clear messages, run their own tests, then report back in under 25 lines: branch name, what was built, test results, open issues.
- **Merge one branch at a time** into `main`, running `uv run pytest` after each merge. The orchestrator resolves conflicts. If a fix changes a contract, update `docs/contracts.md` and tell Cass.
- **Independent review.** After every wave, run the `qa-reviewer` agent, which did not write the code. Fix or re-dispatch every must-fix item before calling the wave done.
- **Stop at wave boundaries.** Report to Cass and wait for "go" before starting the next wave.
- **Push at the end of every session** so work can continue on another machine. Push to the public repo `bowesie92/fleet-fuel-hub` (remote `origin`), whose history starts at one clean public commit. The old working history lives only in the private `bowesie92/fleet-fuel-hub-dev` (remote `dev`) and must never be pushed to the public repo; the pre-push guard (`scripts/public_push_guard.py`) refuses it. The public repo blocks new branches, so merge locally and push `main` only.

## Commands

- Install: `uv sync`
- Tests: `uv run pytest`
- Full pipeline: `uv run fleetfuel build`
- Fetch fresh external data (needs keys): `uv run fleetfuel fetch`
- Generate synthetic data: `uv run fleetfuel synth`
- Preview site locally: `uv run python -m http.server 8000 --directory site`

## Style

- Python 3.12, type hints, pandas and DuckDB SQL.
- **`docs/design/STYLE.md` is required for all site work.** Read it before touching `site/`. Use only the tokens in `site/css/tokens.css`, and section 6 (content rules) overrides anything in the reference mockups.
- Site: plain HTML, CSS and JavaScript, MapLibre GL JS with OpenFreeMap tiles (style `https://tiles.openfreemap.org/styles/liberty`), and Chart.js. No build step for the site.
- Plain-English UI copy for an operations manager, not a developer.

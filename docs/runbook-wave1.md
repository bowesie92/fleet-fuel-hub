# Wave 1 runbook (for the orchestrator)

Phase A is done and pushed. Run these steps only after Cass types "go". Do NOT start Wave 2.

**W1. Launch in parallel.** In ONE message, launch `data-fetcher`, `synth-builder`, `pipeline-builder` and `site-builder` (each with `isolation: worktree`). Give each one its brief from `.claude/agents/`, the contract sections it builds against, and its "done when" criteria:
- data-fetcher: contracts 1, 2, 3, 4.1 to 4.3, 5, 8
- synth-builder: contracts 1, 2, 3, 4 (all), 8, 9
- pipeline-builder: contracts 1, 2, 3, 4 (all), 6, 8, 9
- site-builder: contracts 1, 2, 7, 9

**W2. Merge one at a time** into `main`, in this order: data-fetcher, synth-builder, pipeline-builder, site-builder. Run `uv run pytest` after each merge. Resolve conflicts yourself. If a fix changes a contract, update `docs/contracts.md` (section 10 change log) and tell Cass.

**W3. Integration.** Rerun `uv run fleetfuel synth` with the real fetched inputs, then run ingest and normalize on the real synth output. Confirm the `hub.duckdb` tables are populated. Report station-matching and dedupe accuracy against ground truth (`eval_accuracy`) plus the `dq_log` rows. Commit the regenerated `data/raw/` files.

**W4. Independent review.** Run `qa-reviewer`. Fix or re-dispatch every MUST-FIX item, then rerun qa-reviewer until no MUST-FIX items are left.

**W5. Wrap up.** Add a build-log entry, run `scripts/check_repo_rules.py` (including on the commit message), commit, and push. Then give Cass:
- (a) what got built, in under 10 bullets;
- (b) 3 to 5 concepts to understand from tonight, in plain English, as if preparing to explain them in an interview;
- (c) blockers, such as missing keys;
- (d) the exact prompt to paste next session to run Wave 2: routing-engineer and analytics-builder in parallel, then site pages wired to real data.

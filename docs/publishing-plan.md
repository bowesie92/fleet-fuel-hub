# Publishing plan: a clean public repo

Status: **run Nov 18, 2026** on Cass's go, after qa-reviewer passed v1.16. Public repo: https://github.com/bowesie92/fleet-fuel-hub. Site: https://bowesie92.github.io/fleet-fuel-hub/.

## What happened (Nov 18)

- Step 0: Cass confirmed no other clones exist.
- Step 1: renamed to `fleet-fuel-hub-dev` (PRIVATE); the local remote is `dev`.
- Steps 2 and 3: one commit, `ebf3486`, made from `main` at `b11f7bc` (same tree hash), pushed to a new PRIVATE `fleet-fuel-hub`.
- Step 4, on a fresh clone: forbidden terms (4 checked), secret patterns and em dashes CLEAN; original coordinates CLEAN, including the 38 positions replaced in v1.16; 1 commit, 1 branch, no tags, one no-reply author; with no `.env`, 597 tests passed (4 skipped, they need the local terms list), the build made 0 live API calls and drew all 338 road paths, and `git status` stayed clean.
- Step 5: made public. The branch ruleset went in at once; the tag ruleset was refused once with "Repository has been locked" (a short lock while the visibility change finished) and went in on the retry. Both show `enforcement: active`.
- Step 6: Pages set to Actions, deploy run succeeded; all six pages return 200 with the SYNTHETIC DATA banner and build `06ea23704a5e`.
- Step 7: `origin` is the public repo, old `main` is `dev-main`, every old branch has `pushRemote dev`, `fleetfuel.publicRoot` and `fleetfuel.publicUrl` are set. 33 merged, clean agent worktrees were removed; one with uncommitted changes and two locked ones were kept, and one folder (`.claude/worktrees/agent-ad34c4e9adabf7e92`) could not be fully deleted by Windows and can be removed by hand.
- **Not done: the pre-push hook file.** The session's tool guard blocks writing into the main checkout's `.git` folder, so Cass creates `C:\devleet-fuel-hub\.git\hooks\pre-push` with the two lines in step 7 and runs the guard test there. Until then the server-side rulesets still refuse any push of old history (no new branches or tags, no non-fast-forward to `main`).

## Goal

- The current repo, with its full working history, is renamed `fleet-fuel-hub-dev` and stays private.
- A new public repo, `fleet-fuel-hub`, starts from the cleaned `main` as a single initial commit, so the working history never becomes public.
- GitHub Pages serves the site from the new repo.
- Future work is pushed to the new public repo.

## Before go (done)

- Customer points moved 300 to 600 m off real businesses, every one reachable by truck. The sample, order and offsets are drawn from unseeded randomness and stored nowhere, so the move cannot be replayed (contracts 14.1, 15.1 and 15.4). Every point also has at least 3 candidate businesses 300 to 600 m away (contract 16). `scripts/check_customer_offsets.py --originals-rev 9406634` is clean against all 995 old points.
- The two merged `claude/` branches are deleted on GitHub; only `main` remains.
- Full-history scans of the dev repo are clean: `scripts/check_repo_rules.py --history --secrets`.
- `.github/workflows/pages.yml` skips its deploy job while a repo is private, so it starts on its own once the new repo is public.
- The `gh` login is `bowesie92` with the `repo` and `workflow` scopes. Deleting a repo would need the `delete_repo` scope, which it does not have (see Rollback).

## Steps (on go)

Paths: the main checkout is `C:\dev\fleet-fuel-hub`; `EXPORT` and `VERIFY` are fresh temporary folders.

0. **Cass, before anything else: repoint or remove every OTHER clone.**
   - **Why:** after step 3 the name `bowesie92/fleet-fuel-hub` belongs to the new public repo. Any clone still pointing at it (a second machine, an old copy) would upload the full private history with its first push of a branch.
   - **What to do:** on each such clone, run `git remote set-url origin https://github.com/bowesie92/fleet-fuel-hub-dev.git`, or delete the clone.
   - Confirm it is done before step 3. The orchestrator can only see this machine.
1. **Rename the current repo and repoint the local remote first**, so nothing can push old history to the new name by mistake:
   - `gh repo rename fleet-fuel-hub-dev --repo bowesie92/fleet-fuel-hub --yes`
   - in the main checkout: `git remote rename origin dev` and `git remote set-url dev https://github.com/bowesie92/fleet-fuel-hub-dev.git`
   - `gh repo view bowesie92/fleet-fuel-hub-dev --json visibility` must say PRIVATE.
2. **Build the single clean commit** from `main` without its history:
   - First confirm `git -C C:\dev\fleet-fuel-hub rev-parse main` is the commit named in the go message, the last one qa-reviewer passed. origin/main can lag behind local main.
   - `git -C C:\dev\fleet-fuel-hub archive main` unpacked into `EXPORT`;
   - `git init -b main` there;
   - author "Cass Bowes" with the same GitHub no-reply address as the existing commits;
   - one commit, "Fleet Fuel Hub: initial public release", with the Claude co-author line.
   - Nothing gitignored is included (`.env`, `.local/`, `data/hub.duckdb` are never in an archive).
3. **Create the new repo as PRIVATE first** and push the one commit:
   - `gh repo create bowesie92/fleet-fuel-hub --private --source EXPORT --push --description "Fleet fuel analytics demo on synthetic data"`.
4. **Scan the new repo before it goes public.** Clone it fresh into `VERIFY`, then from the dev checkout run:
   - `uv run python scripts/check_repo_rules.py --history --secrets --repo VERIFY` must print "4 terms checked" with 0 hits, 0 secret patterns, 0 em dashes and RESULT: CLEAN. Without the private list it now fails instead of skipping.
   - `uv run python scripts/check_customer_offsets.py --originals-rev 9406634 --history --repo VERIFY`: CLEAN against all 995 old points. The originals come from the dev repo's history, which never leaves it.
   - `git -C VERIFY rev-list --all --count` is 1, with one branch, no tags, and one author identity (the no-reply address).
   - In `VERIFY`, with no `.env` anywhere above it: `uv sync --locked`, `uv run pytest`, `uv run fleetfuel build` (0 live API calls), then `git status` must be clean. The one exception is `site/data/meta.json`, whose `generated_on` date changes when the build runs on a later day.
   - Any failure stops here: the repo stays private, and Cass decides.
5. **Make it public:** `gh repo edit bowesie92/fleet-fuel-hub --visibility public --accept-visibility-change-consequences`.
5b. **Lock the new repo's refs right after it is public, before Pages and before anyone pushes.** On a free personal plan, rulesets are not enforced on private repos, which is why this comes after step 5. Create two rulesets, then confirm with `gh api repos/bowesie92/fleet-fuel-hub/rulesets` that both show `enforcement: active`:
   - **Branches:** `gh api -X POST repos/bowesie92/fleet-fuel-hub/rulesets --input branches.json`, where `branches.json` is `{"name": "protect branches", "target": "branch", "enforcement": "active", "conditions": {"ref_name": {"include": ["~ALL"], "exclude": []}}, "rules": [{"type": "creation"}, {"type": "deletion"}, {"type": "non_fast_forward"}]}`. This blocks new branches, deletion and force pushes on every branch, `main` included.
   - **Tags:** the same with `"name": "protect tags"` and `"target": "tag"`, so no tag can carry old history either.
   - Pushes to `main` that only add commits still work. Cass can lift the creation rule later for feature branches.
6. **Turn on GitHub Pages:**
   - Set the source to Actions: `gh api -X POST repos/bowesie92/fleet-fuel-hub/pages -f build_type=workflow`.
   - Run the deploy: `gh workflow run Pages --repo bowesie92/fleet-fuel-hub`.
   - Then open `https://bowesie92.github.io/fleet-fuel-hub/` and check every page: SYNTHETIC DATA banner, maps and charts load, no console errors.
7. **Point future work at the public repo** (main checkout):
   - `git remote add origin https://github.com/bowesie92/fleet-fuel-hub.git` and `git fetch origin`.
   - `git branch -m main dev-main` (keeps the old history locally, tracking `dev/main`).
   - `git switch -c main origin/main` (same files, new single-commit history).
   - `git config branch.dev-main.pushRemote dev`, so the old history can only ever go to the private repo. Do the same for every other old local branch (`git for-each-ref refs/heads`), or delete it.
   - Install the push guard, shared by every worktree of this checkout. It refuses any push to the public repo (https or SSH URL) whose history does not start at the public commit, and it fails closed: with no root configured it refuses every push to the public repo.
     - `git -C C:\dev\fleet-fuel-hub config fleetfuel.publicRoot <the public commit>`
     - `git -C C:\dev\fleet-fuel-hub config fleetfuel.publicUrl https://github.com/bowesie92/fleet-fuel-hub.git`
     - Create `C:\dev\fleet-fuel-hub\.git\hooks\pre-push` (no extension; Git for Windows runs it with its own sh) containing exactly these two lines:
       `#!/bin/sh`
       `exec uv run --project "$(git rev-parse --show-toplevel)" python "$(git rev-parse --show-toplevel)/scripts/public_push_guard.py" "$@"`
     - Test it: on a scratch branch made from `dev-main`, `git push --dry-run origin HEAD:refs/heads/guard-test` must be refused by the guard.
   - Remove the stale agent worktrees and their `worktree-agent-*` branches (all merged). The `claude/*` worktree branches stay local with `pushRemote dev` until their sessions are archived.
   - `CLAUDE.md` ("Push at the end of every session" names the public repo) and the README (link to the Pages URL) were updated before step 2, so the public commit already carries them.
   - First push to the public repo is from the new `main`.

## Rollback

- **Before step 5:** the new repo is still private, so nothing is public. Deleting it needs the `delete_repo` scope, which needs Cass's own login: `gh auth refresh -s delete_repo` and then `gh repo delete bowesie92/fleet-fuel-hub`. Then rename `fleet-fuel-hub-dev` back.
- **After step 5:** making a repo private again hides it, but anything already cloned or cached outside GitHub cannot be recalled. That is why every scan runs before step 5.

## What becomes public

One commit holding the current tree:
- code, tests, docs (SPEC, contracts, build log, decisions, this plan), `CLAUDE.md` and `.claude/` agent and settings files;
- synthetic data, EIA prices, OpenStreetMap stations, the moved customer points;
- cached API responses and the site.

No history, no deleted files and no old coordinates. The commit author is the GitHub no-reply address. See `docs/decisions-pending.md` D14 for the content notes.

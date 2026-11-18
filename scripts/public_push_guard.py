#!/usr/bin/env python3
"""Git pre-push guard: never push the private working history to the public repo.

The public repo `fleet-fuel-hub` starts from one clean commit (docs/publishing-plan.md). Every local branch
made before that point carries the private history, which must only ever go to `fleet-fuel-hub-dev`. This
hook refuses a push to the public repo when any pushed commit's history does not start at the public root
commit, recorded once in git config as `fleetfuel.publicRoot`.

Install (publishing plan, step 7), from the main checkout; worktrees share the hook:
    git config fleetfuel.publicRoot <sha of the public initial commit>
    git config fleetfuel.publicUrl https://github.com/bowesie92/fleet-fuel-hub.git
    copy scripts/public_push_guard.py to .git/hooks/pre-push   (or a one-line pre-push that runs it with uv)

Git runs it as: pre-push <remote name> <remote url>, with "<local ref> <local sha> <remote ref> <remote sha>"
lines on stdin. Exit code 1 stops the push.
"""

from __future__ import annotations

import subprocess
import sys

DELETE_SHA = "0" * 40


def git(*args: str) -> str:
    """Run git and return its trimmed output ('' on failure)."""
    done = subprocess.run(["git", *args], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else ""


PUBLIC_REPO = "bowesie92/fleet-fuel-hub"  # owner/name of the public repo; the private one is fleet-fuel-hub-dev


def repo_path(url: str) -> str:
    """'owner/name' from an https or SSH GitHub URL (git@github.com:owner/name.git), lower-cased; '' if not GitHub."""
    u = url.strip().rstrip("/").removesuffix(".git").lower()
    for prefix in ("https://github.com/", "http://github.com/", "ssh://git@github.com/", "git@github.com:"):
        if u.startswith(prefix):
            return u[len(prefix):]
    return ""


def same_repo(url_a: str, url_b: str) -> bool:
    """True when two remote URLs (https or SSH) point at the same GitHub repo."""
    return bool(repo_path(url_a)) and repo_path(url_a) == repo_path(url_b)


def is_public_target(remote_url: str, public_url: str) -> bool:
    """Treat the push as going to the public repo when it matches the configured URL, or, failing closed when
    none is configured, when it names the public repo itself."""
    if public_url:
        return same_repo(remote_url, public_url)
    return repo_path(remote_url) == PUBLIC_REPO


def roots(sha: str) -> set[str]:
    """The root commits a commit's history starts from."""
    return set(git("rev-list", "--max-parents=0", sha).split())


def check(remote_url: str, lines: list[str], public_url: str, public_root: str) -> list[str]:
    """The refs that must not be pushed; an empty list means the push is safe."""
    if not is_public_target(remote_url, public_url):
        return []
    refused = []
    for line in lines:
        parts = line.split()
        if len(parts) != 4 or parts[1] == DELETE_SHA:
            continue
        local_ref, local_sha = parts[0], parts[1]
        if not public_root or roots(local_sha) != {public_root}:
            refused.append(local_ref)
    return refused


def main(argv: list[str]) -> int:
    """Refuse the push when a pushed ref carries history from before the public root commit."""
    remote_url = argv[2] if len(argv) > 2 else ""
    public_url = git("config", "--get", "fleetfuel.publicUrl")
    public_root = git("config", "--get", "fleetfuel.publicRoot")
    refused = check(remote_url, sys.stdin.read().splitlines(), public_url, public_root)
    if refused:
        print("public_push_guard: refusing to push to the public repo. These refs carry private history "
              f"(they do not start at the public root commit): {', '.join(refused)}. "
              "Push old branches to the dev remote instead.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""Repo rule checks run before every commit and by the qa-reviewer.

Checks every file git would commit (tracked plus untracked, not ignored) for:
1. Forbidden terms listed in .local/forbidden_terms.txt (gitignored), looked up in
   this checkout or any parent folder, so agent worktrees use the main checkout's list.
   Reports hit counts and file paths only. It never prints the terms.
2. Em dashes (CLAUDE.md hard rule 10).
3. With --secrets: secret-shaped patterns (tokens, private keys, key assignments). Patterns only:
   no script ever reads the real key values (CLAUDE.md hard rule 12).
With --history it scans every file version reachable from any ref, plus every commit message and
author identity, instead of the current files. With --repo it scans another clone (for example the
public repo before it goes public), still using the terms list found from this checkout.

Usage:
    uv run python scripts/check_repo_rules.py
    uv run python scripts/check_repo_rules.py --message-file .git/COMMIT_EDITMSG
    uv run python scripts/check_repo_rules.py --history --secrets [--repo PATH]
Exit code 0 means clean, 1 means at least one problem was found.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TERMS_REL = Path(".local") / "forbidden_terms.txt"


def find_terms_file(start: Path = REPO_ROOT) -> Path | None:
    """Find the private forbidden-terms list in this checkout or any parent folder.

    Agent worktrees live inside the main checkout (.claude/worktrees/...), so the
    list kept in the main checkout is found from there too. Returns None if absent.
    """
    for folder in [start, *start.parents]:
        candidate = folder / TERMS_REL
        if candidate.is_file():
            return candidate
    return None


TERMS_FILE = find_terms_file()
EM_DASH = chr(0x2014)  # the em dash, built from its code point so this file stays clean
NEVER_SCAN = {".env", ".local/forbidden_terms.txt"}


def candidate_files(root: Path = REPO_ROOT) -> list[Path]:
    """List the files that would end up in a commit: tracked plus untracked-but-not-ignored."""
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout
    paths = []
    for line in out.splitlines():
        rel = line.strip()
        if rel and rel not in NEVER_SCAN and (root / rel).is_file():
            paths.append(root / rel)
    return paths


def read_text(path: Path) -> str | None:
    """Read a file as UTF-8 text, or return None for binary files."""
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def load_terms(terms_file: Path | None = TERMS_FILE) -> list[str]:
    """Load the forbidden terms, lower-cased, skipping blanks. Empty list if the file is absent."""
    if terms_file is None or not terms_file.exists():
        return []
    lines = terms_file.read_text(encoding="utf-8").splitlines()
    return [t.strip().lower() for t in lines if t.strip()]


def forbidden_term_hits(texts: dict[str, str], terms: list[str]) -> dict[str, dict[int, int]]:
    """Count case-insensitive hits per file, keyed by term number (1-based), never by the term."""
    hits: dict[str, dict[int, int]] = {}
    for name, text in texts.items():
        lower = text.lower()
        for i, term in enumerate(terms, start=1):
            n = lower.count(term)
            if n:
                hits.setdefault(name, {})[i] = n
    return hits


def em_dash_hits(texts: dict[str, str]) -> dict[str, list[int]]:
    """Return the line numbers that contain an em dash, per file."""
    hits: dict[str, list[int]] = {}
    for name, text in texts.items():
        lines = [i for i, line in enumerate(text.splitlines(), start=1) if EM_DASH in line]
        if lines:
            hits[name] = lines
    return hits


def collect_texts(extra_message_file: Path | None = None) -> dict[str, str]:
    """Gather the text of every committable file, plus an optional commit message file."""
    texts: dict[str, str] = {}
    for path in candidate_files():
        text = read_text(path)
        if text is not None:
            texts[path.relative_to(REPO_ROOT).as_posix()] = text
    if extra_message_file is not None:
        texts[f"<commit message: {extra_message_file.name}>"] = extra_message_file.read_text(encoding="utf-8")
    return texts


SECRET_PATTERNS = {
    "GitHub token": r"gh[pousr]_[A-Za-z0-9]{30,}",
    "sk- style API key": r"\bsk-[A-Za-z0-9_-]{20,}",
    "AWS access key": r"AKIA[0-9A-Z]{16}",
    "private key block": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "key, token, secret or password set to a long value":
        r"(?i)(api[_-]?key|access[_-]?token|secret|password)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{20,}",
    "bearer token": r"(?i)bearer\s+[A-Za-z0-9_\-\.]{20,}",
    "api_key in a URL": r"(?i)api_key=[A-Za-z0-9]{20,}",
}


def secret_hits(texts: dict[str, str]) -> dict[str, list[str]]:
    """Which secret-shaped patterns appear in which texts. Reports pattern names only, never the matched text."""
    compiled = {label: re.compile(pattern) for label, pattern in SECRET_PATTERNS.items()}
    hits: dict[str, list[str]] = {}
    for name, text in texts.items():
        found = [label for label, rx in compiled.items() if rx.search(text)]
        if found:
            hits[name] = found
    return hits


def git_bytes(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    """Run a git command in `repo` and return its raw output."""
    return subprocess.run(["git", *args], cwd=repo, input=data, capture_output=True, check=True).stdout


def history_texts(repo: Path) -> dict[str, str]:
    """Every file version reachable from any ref, every commit message and every author identity.

    This is what anyone who can clone the repo receives, history included. File versions are named
    by path plus a short blob id; binary files are skipped.
    """
    paths: dict[str, set[str]] = {}
    for line in git_bytes(repo, "rev-list", "--all", "--objects").decode("utf-8", "replace").splitlines():
        sha, _, path = line.partition(" ")
        if path:
            paths.setdefault(sha, set()).add(path)
    query = "\n".join(paths).encode()
    kinds = git_bytes(repo, "cat-file", "--batch-check=%(objectname) %(objecttype)", data=query).decode().splitlines()
    blobs = [k.split()[0] for k in kinds if k.split()[1] == "blob"]
    texts: dict[str, str] = {}
    raw = git_bytes(repo, "cat-file", "--batch", data="\n".join(blobs).encode())
    pos = 0
    while pos < len(raw):
        end = raw.index(b"\n", pos)
        sha, _, size = raw[pos:end].decode().split()
        body = raw[end + 1:end + 1 + int(size)]
        pos = end + 1 + int(size) + 1
        path = sorted(paths[sha])[0]
        if path in NEVER_SCAN:
            continue
        try:
            texts[f"{path} @{sha[:8]}"] = body.decode("utf-8")
        except UnicodeDecodeError:
            continue
    log = git_bytes(repo, "log", "--all", "--format=%h%x00%an <%ae>%x00%cn <%ce>%x00%B%x01").decode("utf-8", "replace")
    for entry in log.split("\x01"):
        if entry.strip():
            short = entry.strip().split("\x00", 1)[0]
            texts[f"<commit {short}: message and authors>"] = entry
    return texts


def report_secrets(texts: dict[str, str]) -> int:
    """Print the secret-pattern findings (pattern names and places only) and return how many were found."""
    found = secret_hits(texts)
    total = sum(len(v) for v in found.values())
    print(f"secret patterns: {len(SECRET_PATTERNS)} patterns checked across {len(texts)} texts, {total} hits")
    for name, labels in sorted(found.items()):
        print(f"  {name}: {', '.join(labels)}")
    return total


def main(argv: list[str] | None = None) -> int:
    """Run the checks and print a short, term-free report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--message-file", type=Path, default=None, help="also scan a commit message file")
    parser.add_argument("--history", action="store_true", help="scan every file version, commit message and author in history")
    parser.add_argument("--secrets", action="store_true", help="also scan for secret-shaped patterns (patterns only)")
    parser.add_argument("--repo", type=Path, default=None, help="scan this other clone instead of this checkout")
    args = parser.parse_args(argv)

    repo = (args.repo or REPO_ROOT).resolve()
    texts = history_texts(repo) if args.history else collect_texts(args.message_file)
    if args.history:
        print(f"history mode: {repo.name}, {len(texts)} texts (file versions, commit messages and authors)")
    terms = load_terms()
    problems = 0

    if not terms and (args.history or args.repo):
        print("forbidden terms: MISSING (.local/forbidden_terms.txt not found); a history or release scan cannot pass without it")
        problems += 1
    elif not terms:
        print("forbidden terms: SKIPPED (.local/forbidden_terms.txt not found)")
    else:
        term_hits = forbidden_term_hits(texts, terms)
        total = sum(sum(v.values()) for v in term_hits.values())
        print(f"forbidden terms: {len(terms)} terms checked across {len(texts)} files, {total} hits")
        for name, per_term in sorted(term_hits.items()):
            detail = ", ".join(f"term #{i}: {n}" for i, n in sorted(per_term.items()))
            print(f"  {name}: {detail}")
        problems += total

    if args.secrets:
        problems += report_secrets(texts)

    dash_hits = em_dash_hits(texts)
    total_dash = sum(len(v) for v in dash_hits.values())
    print(f"em dashes: {total_dash} lines")
    for name, lines in sorted(dash_hits.items()):
        print(f"  {name}: lines {lines[:10]}{' ...' if len(lines) > 10 else ''}")
    problems += total_dash

    print("RESULT:", "CLEAN" if problems == 0 else "PROBLEMS FOUND")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

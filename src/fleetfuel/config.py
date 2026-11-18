"""Shared settings for every step: where files live, and how to read the assumptions.

Every business number (MPG, driver cost, thresholds, messiness rates...) lives in
config/assumptions.yaml. Code reads it through `load_assumptions()` and `get()`, so
there are no magic numbers scattered through the pipeline. `describe_assumptions()`
also returns each value with its one-line note, so the site can print them.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "assumptions.yaml"
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
GROUND_TRUTH_DIR = RAW_DIR / "ground_truth"
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "hub.duckdb"
SITE_DIR = REPO_ROOT / "site"
SITE_DATA_DIR = SITE_DIR / "data"


def load_assumptions(path: Path | None = None) -> dict[str, Any]:
    """Read the assumptions file into a nested dictionary of plain values."""
    with open(path or CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def get(cfg: dict[str, Any], dotted_key: str) -> Any:
    """Look up one assumption by dotted name, e.g. get(cfg, "savings.reserve_gal").

    Fails loudly with the missing key's name, so a missing assumption is never
    silently replaced by a guess.
    """
    node: Any = cfg
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"Assumption '{dotted_key}' is missing from config/assumptions.yaml")
        node = node[part]
    return node


def load_env() -> None:
    """Load API keys from the nearest .env, searching upward from the repo root.

    Searching upward means agents working in a worktree under .claude/worktrees/
    still find the main checkout's .env. Values are never printed.
    """
    for folder in [REPO_ROOT, *REPO_ROOT.parents]:
        candidate = folder / ".env"
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return


def has_key(name: str) -> bool:
    """True if an API key is set and non-empty, without revealing its value."""
    load_env()
    return bool(os.environ.get(name, "").strip())


def _split_comment(line: str) -> tuple[str, str]:
    """Split a YAML line into (content, comment), ignoring '#' inside quotes."""
    in_single = in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double and (i == 0 or line[i - 1].isspace()):
            return line[:i].rstrip(), line[i + 1 :].strip()
    return line.rstrip(), ""


def describe_assumptions(path: Path | None = None) -> list[dict[str, Any]]:
    """List every leaf assumption as {key, value, note}, in file order.

    `key` is the dotted name, `value` is the parsed value, and `note` is the
    one-line comment written next to it. The site's Assumptions page prints this.
    """
    text = Path(path or CONFIG_PATH).read_text(encoding="utf-8")
    stack: list[tuple[int, str]] = []
    rows: list[dict[str, Any]] = []
    for raw in text.splitlines():
        content, note = _split_comment(raw)
        if not content.strip():
            continue
        indent = len(content) - len(content.lstrip(" "))
        key, sep, value = content.strip().partition(":")
        if not sep:
            continue
        while stack and stack[-1][0] >= indent:
            stack.pop()
        dotted = ".".join([k for _, k in stack] + [key.strip()])
        if value.strip() == "":
            stack.append((indent, key.strip()))
        else:
            rows.append({"key": dotted, "value": yaml.safe_load(value), "note": note})
    return rows

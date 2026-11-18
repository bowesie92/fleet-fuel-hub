"""Shared helpers for fetching public data: the cache, secret scrubbing and polite retries.

In business terms: every call to an outside service is saved to a file under
data/cache/ so the next build reads the file instead of calling the service. Keys never
reach those files, and a failed call reports what went wrong without echoing the key.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

import requests

from fleetfuel import config

# Parameter names that carry credentials. They are removed from anything stored or hashed.
SECRET_NAMES = frozenset({"api_key"})

# Statuses that mean "the service is busy, try again later".
RETRY_STATUSES = frozenset({429, 502, 503, 504})


def pause(seconds: float) -> None:
    """Wait politely between calls (one place, so tests can switch the waiting off)."""
    if seconds > 0:
        time.sleep(seconds)


def retry_settings(cfg: dict) -> tuple[int, float]:
    """(max retries, first wait in seconds) from fetch.retry.*; a missing key raises."""
    return int(config.get(cfg, "fetch.retry.max_retries")), float(config.get(cfg, "fetch.retry.wait_s"))


def timeouts(cfg: dict, min_read_s: float = 0.0) -> tuple[float, float]:
    """(connect, read) timeouts in seconds from fetch.*; the read wait is at least `min_read_s`."""
    connect = float(config.get(cfg, "fetch.connect_timeout_s"))
    read = float(config.get(cfg, "fetch.read_timeout_s"))
    return connect, max(read, min_read_s)


def strip_secrets(obj: Any) -> Any:
    """Return a copy of a params dict or JSON value with every credential entry removed."""
    if isinstance(obj, dict):
        return {k: strip_secrets(v) for k, v in obj.items() if k not in SECRET_NAMES}
    if isinstance(obj, list):
        return [strip_secrets(v) for v in obj]
    return obj


def request_hash(path: str, params: dict[str, Any] | None = None) -> str:
    """Short, stable name for one request. The key is removed first, so it never affects the name."""
    canonical = json.dumps({"path": path, "params": strip_secrets(params or {})}, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def read_json(path: Path) -> Any | None:
    """Read a cached JSON file, or None when it is not there yet."""
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_text(path: Path, text: str) -> None:
    """Write UTF-8 text with plain \\n line endings, creating the folder if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def write_json(path: Path, payload: Any, indent: int | None = 1) -> None:
    """Write JSON to the cache with a trailing newline."""
    write_text(path, json.dumps(payload, ensure_ascii=False, indent=indent) + "\n")


def ensure_no_secret(text: str, secret: str) -> None:
    """Refuse to store text that contains a key. A last safety net before anything is cached."""
    if secret and secret in text:
        raise RuntimeError("Refusing to cache a response that contains an API key.")


def send_with_backoff(
    send: Callable[[], requests.Response],
    *,
    max_retries: int,
    wait_s: float,
    label: str,
) -> requests.Response:
    """Call a service, waiting longer after each 'busy' answer (429, 502, 503, 504) or timeout.

    Errors name the service (`label`) and the status only, never the full URL, because a URL
    can carry an API key.
    """
    last = "no attempt made"
    for attempt in range(max_retries + 1):
        try:
            resp = send()
        except (requests.Timeout, requests.ConnectionError) as exc:
            last = type(exc).__name__
        except requests.RequestException as exc:
            raise RuntimeError(f"{label} request failed ({type(exc).__name__}).") from None
        else:
            if resp.status_code not in RETRY_STATUSES:
                if resp.status_code >= 400:
                    raise RuntimeError(f"{label} answered with HTTP {resp.status_code}.")
                return resp
            last = f"HTTP {resp.status_code}"
        if attempt < max_retries:
            wait = wait_s * (2**attempt)
            print(f"[fetch] {label} busy ({last}); waiting {wait:.0f}s before retry {attempt + 1}")
            pause(wait)
    raise RuntimeError(f"{label} still failing after {max_retries} retries (last: {last}).")

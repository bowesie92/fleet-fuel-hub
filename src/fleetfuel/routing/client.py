"""The openrouteservice client: every request goes through a cache of its own answers.

In business terms: asking a routing service for a road distance costs a free-plan call, and the
plan only allows a few hundred a day. So each request is saved to `data/cache/ors/` the first time
and read from there ever after. The saved name is a hash of the request body (the coordinates and
options), never the key and never the host, so a fresh clone with no key rebuilds from the cache
and a host change does not throw the cache away.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

import requests

from fleetfuel import config

KEY_ENV = "ORS_API_KEY"
# Path layout on the HeiGIT host, from the HeiGIT notice on the move off api.openrouteservice.org:
# <base>/openrouteservice/v2/<service>/<profile>[/geojson]. The host itself lives only in config.
PATH_PREFIX = "/openrouteservice/v2"
DIRECTIONS_PATH = PATH_PREFIX + "/directions/{profile}/geojson"
MATRIX_PATH = PATH_PREFIX + "/matrix/{profile}"
SNAP_PATH = PATH_PREFIX + "/snap/{profile}/json"
SNAP_MAX_LOCATIONS = 5000  # Fact: hosted-API limit of locations per snap request (openrouteservice hosted API restrictions page)
HTTP_TIMEOUT_S = 120  # transport timeout for one call, not a business assumption
RATE_LIMITED = 429  # HTTP status openrouteservice returns for too many calls in a minute
HASH_CHARS = 16
KINDS = ("directions", "matrix", "snap")
ROUTABLE_CODES = (2010, 6010)  # openrouteservice error codes for "point not found" (routing, matrix)
ROUTABLE_PHRASE = "routable point"
POINT_INDEX_PATTERN = re.compile(r"coordinate (\d+)")


class RoutableError(RuntimeError):
    """openrouteservice could not place a waypoint on a truck road; `index` is the waypoint it named, if any."""

    def __init__(self, message: str, index: int | None = None) -> None:
        super().__init__(message)
        self.index = index


def _routable_error(status: int, text: str) -> RoutableError | None:
    """A RoutableError when an error answer says a point could not be snapped to a road, else None."""
    try:
        err = json.loads(text).get("error", {})
        code, message = err.get("code"), str(err.get("message", ""))
    except (ValueError, AttributeError):
        code, message = None, text
    if code not in ROUTABLE_CODES and ROUTABLE_PHRASE not in message:
        return None
    found = POINT_INDEX_PATTERN.search(message)
    return RoutableError(f"openrouteservice could not find a routable point (HTTP {status}): {message[:200]}",
                         int(found.group(1)) if found else None)


def _optional(cfg: dict, key: str, default: Any) -> Any:
    """An assumption that may not be in the config yet; the default is used only when the key is absent."""
    try:
        return config.get(cfg, key)
    except KeyError:
        return default


def cache_dir_default() -> Path:
    """Where cached openrouteservice responses live (contract section 5)."""
    return config.CACHE_DIR / "ors"


def request_hash(kind: str, profile: str, body: dict) -> str:
    """Short hash of the request alone (kind, profile and body), so no key or host can leak into the cache."""
    text = json.dumps({"kind": kind, "profile": profile, "body": body}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:HASH_CHARS]


class OrsClient:
    """Cache-first openrouteservice caller that counts live calls and respects the free-plan limits."""

    def __init__(
        self,
        cfg: dict,
        cache_dir: Path | None = None,
        post: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = str(config.get(cfg, "routing.ors_base_url")).rstrip("/")
        self.profile = str(config.get(cfg, "routing.ors_profile"))
        self.pause_s = float(config.get(cfg, "routing.ors_pause_s"))
        self.max_cells = int(config.get(cfg, "routing.max_matrix_cells"))
        self.limits = {
            "directions": int(config.get(cfg, "routing.daily_directions_limit")),
            "matrix": int(config.get(cfg, "routing.daily_matrix_limit")),
            # Optional keys: until config gains them, snap shares the (smaller) matrix daily limit and the documented batch size.
            "snap": int(_optional(cfg, "routing.daily_snap_limit", config.get(cfg, "routing.daily_matrix_limit"))),
        }
        self.snap_batch = int(_optional(cfg, "routing.snap_batch_size", SNAP_MAX_LOCATIONS))
        self.cache_dir = Path(cache_dir) if cache_dir else cache_dir_default()
        self._post = post or requests.post
        self._sleep = sleep
        self.live = {k: 0 for k in KINDS}
        self.cached = {k: 0 for k in KINDS}
        self.snap_radius_m = int(config.get(cfg, "routing.snap_retry_radius_m"))
        self.rate_limit_wait_s = float(config.get(cfg, "routing.rate_limit_wait_s"))
        self.rate_limit_tries = int(config.get(cfg, "routing.rate_limit_tries"))
        self.max_retry_after_s = float(config.get(cfg, "routing.max_retry_after_s"))
        self.used: set[Path] = set()  # every cache file read or written, for pruning

    # ---------------------------------------------------------------- cache

    def _cache_file(self, kind: str, body: dict) -> Path:
        return self.cache_dir / f"{kind}_{request_hash(kind, self.profile, body)}.json"

    @staticmethod
    def _read_cache(path: Path) -> dict | None:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))["response"]

    def _write_cache(self, path: Path, kind: str, body: dict, response: dict) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        record = {"kind": kind, "profile": self.profile, "request": body, "response": response}
        path.write_text(json.dumps(record, separators=(",", ":")), encoding="utf-8")

    # ---------------------------------------------------------------- live call

    @staticmethod
    def _key(kind: str) -> str:
        config.load_env()
        key = os.environ.get(KEY_ENV, "").strip()
        if not key:
            raise RuntimeError(
                f"A new {kind} request is needed but is not in data/cache/ors/, and {KEY_ENV} is not set. "
                f"Add your openrouteservice key to .env (see .env.example) and run the build again."
            )
        return key

    # One clock for the whole process: every client (export makes a new one per detour leg) waits its turn,
    # so the pause between live calls holds no matter how many clients exist.
    _last_live_at: float | None = None

    def _wait_turn(self) -> None:
        """Pause so live calls from any client in this run are at least `routing.ors_pause_s` apart."""
        last = OrsClient._last_live_at
        if last is not None:
            remaining = self.pause_s - (time.monotonic() - last)
            if remaining > 0:
                self._sleep(remaining)
        OrsClient._last_live_at = time.monotonic()

    def backoff_s(self, resp: Any, attempt: int) -> float:
        """How long to wait after a 429: the server's Retry-After seconds when it sends one, otherwise
        `routing.rate_limit_wait_s` doubled for each earlier retry (65 s, then 130 s)."""
        header = (getattr(resp, "headers", None) or {}).get("Retry-After")
        try:
            if header is not None and float(header) >= 0:
                return float(header)
        except ValueError:
            pass  # an HTTP-date Retry-After is not used by openrouteservice; fall back to the backoff
        return self.rate_limit_wait_s * (2 ** attempt)

    def _post_politely(self, kind: str, url: str, body: dict, headers: dict) -> Any:
        """One live request through the shared limiter. Every POST attempt, retries included, counts toward
        the daily total. On HTTP 429 (too many calls this minute) it backs off and tries again, at most
        `routing.rate_limit_tries` tries in all. A server that sends a Retry-After for longer than
        `routing.max_retry_after_s` is not waited on: that means the daily quota is probably used up."""
        for attempt in range(self.rate_limit_tries):
            if self.live[kind] >= self.limits[kind]:
                raise RuntimeError(f"Stopping before the free-plan limit of {self.limits[kind]} {kind} calls a day. Run again tomorrow.")
            self._wait_turn()
            self.live[kind] += 1
            try:
                resp = self._post(url, json=body, headers=headers, timeout=HTTP_TIMEOUT_S)
            except requests.RequestException as exc:
                raise RuntimeError(f"Could not reach openrouteservice for a {kind} request: {type(exc).__name__}.") from exc
            if resp.status_code != RATE_LIMITED:
                return resp
            wait = self.backoff_s(resp, attempt)
            if (getattr(resp, "headers", None) or {}).get("Retry-After") is not None and wait > self.max_retry_after_s:
                raise RuntimeError(
                    f"openrouteservice asked to wait {wait:.0f} s before the next {kind} request (HTTP 429), "
                    f"longer than the {self.max_retry_after_s:.0f} s this build will wait. "
                    f"The daily quota may be used up. Run again tomorrow."
                )
            if attempt == self.rate_limit_tries - 1:
                return resp
            print(f"[routing] openrouteservice says too many calls this minute; waiting {wait:.0f} s (try {attempt + 1} of {self.rate_limit_tries})")
            self._sleep(wait)
        return resp

    def _live(self, kind: str, path: str, body: dict) -> dict:
        key = self._key(kind)
        url = self.base_url + path.format(profile=self.profile)
        headers = {"Authorization": key, "Content-Type": "application/json", "Accept": "application/json, application/geo+json"}
        resp = self._post_politely(kind, url, body, headers)
        if resp.status_code != 200:
            routable = _routable_error(resp.status_code, resp.text)
            if routable is not None:
                raise routable
            raise RuntimeError(f"openrouteservice {kind} request failed with HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def request(self, kind: str, path: str, body: dict) -> dict:
        """Return the response for this request: from the cache if saved, else one live call (then saved)."""
        file = self._cache_file(kind, body)
        hit = self._read_cache(file)
        if hit is not None and "error" not in hit:
            self.used.add(file)
            self.cached[kind] += 1
            return hit
        if hit is None:
            try:
                response = self._live(kind, path, body)
            except RoutableError as exc:
                # Remember the refusal under the normal body, so a rerun does not spend a call asking again.
                hit = {"error": {"routable": str(exc), "index": exc.index}}
                self._write_cache(file, kind, body, hit)
            else:
                self._write_cache(file, kind, body, response)
                self.used.add(file)
                return response
        self.used.add(file)
        # The normal request was refused: matrix has no radius option, directions retries with a wider one.
        err = hit["error"]
        if kind != "directions":
            raise RoutableError(err["routable"], err.get("index"))
        return self._retry_wider(kind, path, body)

    def _retry_wider(self, kind: str, path: str, body: dict) -> dict:
        """Second try for a directions request: every waypoint may snap to a road up to the retry radius away. Cached on its own body."""
        retry = dict(body, radiuses=[self.snap_radius_m] * len(body["coordinates"]))
        file = self._cache_file(kind, retry)
        hit = self._read_cache(file)
        if hit is not None:
            self.used.add(file)
            self.cached[kind] += 1
            if "error" in hit:
                raise RoutableError(hit["error"]["routable"], hit["error"].get("index"))
            return hit
        try:
            response = self._live(kind, path, retry)
        except RoutableError as exc:
            # Even the wider search was refused: remember it, so a rerun spends no call on this point again.
            self._write_cache(file, kind, retry, {"error": {"routable": str(exc), "index": exc.index}})
            self.used.add(file)
            raise
        self._write_cache(file, kind, retry, response)
        self.used.add(file)
        return response

    # ---------------------------------------------------------------- the two services

    def directions(self, coords: list[tuple[float, float]]) -> dict:
        """Road route through the (lat, lon) points in order: {"meters", "seconds", "geometry"}."""
        body = {"coordinates": [[round(lon, 6), round(lat, 6)] for lat, lon in coords], "instructions": False}
        data = self.request("directions", DIRECTIONS_PATH, body)
        feature = data["features"][0]
        summary = feature["properties"]["summary"]
        return {"meters": float(summary["distance"]), "seconds": float(summary["duration"]), "geometry": feature["geometry"]}

    def matrix(self, coords: list[tuple[float, float]]) -> dict:
        """Full square road matrix between the (lat, lon) points: {"meters": NxN, "seconds": NxN} (None where unroutable)."""
        if len(coords) ** 2 > self.max_cells:
            raise ValueError(f"A matrix of {len(coords)} points is {len(coords) ** 2} cells, over the limit of {self.max_cells}.")
        body = {
            "locations": [[round(lon, 6), round(lat, 6)] for lat, lon in coords],
            "metrics": ["distance", "duration"],
            "units": "m",
        }
        return self._matrix_skipping_unsnappable(body, len(coords))

    def snap(self, coords: list[tuple[float, float]], radius_m: float) -> list[bool]:
        """For each (lat, lon) point, True when it snaps to a truck road within `radius_m`, in input order.

        One snap request per batch of at most `snap_batch` points, each cached by its own body. A null or
        missing entry in the answer means no road was found, so it is False.
        """
        out: list[bool] = []
        for start in range(0, len(coords), self.snap_batch):
            chunk = coords[start:start + self.snap_batch]
            body = {"locations": [[round(lon, 6), round(lat, 6)] for lat, lon in chunk], "radius": radius_m}
            found = (self.request("snap", SNAP_PATH, body) or {}).get("locations") or []
            out.extend(i < len(found) and found[i] is not None for i in range(len(chunk)))
        return out

    def _matrix_skipping_unsnappable(self, body: dict, total: int) -> dict:
        """Matrix call. The matrix service has no radius option (it only says "point not found"), so a point it
        cannot place on a road is left out of the request and shown as None (unroutable) in the result.
        Each smaller request is cached under its own body. Points are dropped one at a time.
        """
        kept = list(range(total))
        locations = body["locations"]
        while True:
            sub = dict(body, locations=[locations[i] for i in kept])
            try:
                data = self.request("matrix", MATRIX_PATH, sub)
                break
            except RoutableError as exc:
                if exc.index is None or not 0 <= exc.index < len(kept) or len(kept) <= 1:
                    raise
                print(f"[routing] matrix point {kept[exc.index]} is not on a truck road; treating it as unroutable.")
                del kept[exc.index]
        grid = {"meters": [[None] * total for _ in range(total)], "seconds": [[None] * total for _ in range(total)]}
        for a, i in enumerate(kept):
            for b, j in enumerate(kept):
                grid["meters"][i][j] = data["distances"][a][b]
                grid["seconds"][i][j] = data["durations"][a][b]
        return grid

    def summary(self) -> str:
        """One line for the build log: live and cached calls by service."""
        parts = [f"{k}: {self.live[k]} live, {self.cached[k]} from cache" for k in KINDS]
        return "openrouteservice calls, " + "; ".join(parts)

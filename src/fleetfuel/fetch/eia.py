"""EIA weekly retail diesel prices (contract 4.1).

In business terms: the U.S. Energy Information Administration publishes a weekly average
pump price for on-highway No. 2 diesel in the West Coast (PADD 5) region without California,
which includes Arizona. We do not hard-code the series ID. We walk the EIA API's own
metadata (route, then the `duoarea` and `product` facet lists), pick the facet values whose
names match the region and product in config/assumptions.yaml, and read the series ID off the
data itself. The chosen series is written to data/cache/eia/series_meta.json.

Source: U.S. Energy Information Administration (EIA). Every chart that shows these prices
must carry that caption (CLAUDE.md rule 11).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import re
from pathlib import Path
from typing import Any

import requests

from fleetfuel import config
from fleetfuel.fetch import cache

# The EIA route that holds weekly gasoline and diesel retail prices (Fact: from the API's route list).
ROUTE = "petroleum/pri/gnd"
CSV_COLUMNS = ["week_start", "price_per_gal", "series_id"]
KEY_NAME = "EIA_API_KEY"


class MissingKeyError(RuntimeError):
    """Raised when EIA data is not cached and EIA_API_KEY is not set."""


class EiaClient:
    """Calls the EIA API v2 with a cache in front: a request already on disk is never repeated."""

    def __init__(self, cfg: dict, cache_dir: Path, refresh: bool = False):
        self.base = config.get(cfg, "fetch.eia.api_base").rstrip("/")
        self.pause_s = float(config.get(cfg, "fetch.eia.pause_s"))
        self.max_retries, self.retry_wait_s = cache.retry_settings(cfg)
        self.timeout = cache.timeouts(cfg)
        self.cache_dir = cache_dir
        self.refresh = refresh
        self.network_calls = 0

    def _cache_path(self, path: str, params: dict[str, Any]) -> Path:
        return self.cache_dir / f"{cache.request_hash(path, params)}.json"

    def _api_key(self) -> str:
        config.load_env()
        key = os.environ.get(KEY_NAME, "").strip()
        if not key:
            raise MissingKeyError(
                f"{KEY_NAME} is not set and the EIA data is not in data/cache/eia/. "
                "Add the key to .env (see .env.example) and run `uv run fleetfuel fetch` again."
            )
        return key

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        """Return the JSON for one EIA request, from the cache if present, else from the API."""
        params = dict(params or {})
        target = self._cache_path(path, params)
        if not self.refresh:
            hit = cache.read_json(target)
            if hit is not None:
                return hit
        key = self._api_key()
        if self.network_calls:
            cache.pause(self.pause_s)
        url = f"{self.base}/{path.strip('/')}"
        resp = cache.send_with_backoff(
            lambda: requests.get(url, params={**params, "api_key": key}, timeout=self.timeout),
            max_retries=self.max_retries,
            wait_s=self.retry_wait_s,
            label=f"EIA /{path.strip('/')}",
        )
        self.network_calls += 1
        payload = cache.strip_secrets(resp.json())
        # The body may echo the request; we store the scrubbed payload and check it is clean.
        cache.ensure_no_secret(json.dumps(payload), key)
        cache.write_json(target, payload)
        return payload


def _words(text: str) -> set[str]:
    """Lower-case words of a label, ignoring punctuation (so 'PADD 5 EXCEPT CALIFORNIA' is comparable)."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def pick_facet(facets: list[dict], description: str) -> dict:
    """Choose the facet value whose name best matches a plain-English description.

    An exact word-for-word match wins. Otherwise the value whose words are all contained in the
    description and that uses the most of them wins (so 'PADD 5 EXCEPT CALIFORNIA' beats 'PADD 5'
    for 'West Coast (PADD 5) except California'). No match raises an error: we never guess.
    """
    want = _words(description)
    exact = [f for f in facets if _words(f["name"]) == want]
    if exact:
        return exact[0]
    inside = [f for f in facets if _words(f["name"]) and _words(f["name"]) <= want]
    if not inside:
        raise LookupError(f"No EIA facet value matches '{description}'.")
    inside.sort(key=lambda f: (-len(_words(f["name"])), f["id"]))
    best = inside[0]
    # A tie on word count between different values would be a guess.
    ties = [f for f in inside if len(_words(f["name"])) == len(_words(best["name"]))]
    if len(ties) > 1:
        raise LookupError(f"More than one EIA facet value matches '{description}'.")
    return best


def discover_facets(client: EiaClient, cfg: dict) -> dict[str, Any]:
    """Walk the route metadata and facet lists to find the region and product for the series.

    Steps: confirm the route exists under its parents, confirm it offers weekly data, then
    list the `duoarea` and `product` facet values and pick the ones that match the config.
    """
    parts = ROUTE.split("/")
    for depth in range(1, len(parts)):
        parent = "/".join(parts[:depth])
        routes = client.get(parent)["response"].get("routes", [])
        if parts[depth] not in {r["id"] for r in routes}:
            raise LookupError(f"EIA route /{'/'.join(parts[: depth + 1])} not found under /{parent}.")
    meta = client.get(ROUTE)["response"]
    if "weekly" not in {f["id"] for f in meta.get("frequency", [])}:
        raise LookupError(f"EIA route /{ROUTE} does not offer weekly data.")
    have = {f["id"] for f in meta.get("facets", [])}
    if not {"duoarea", "product"} <= have:
        raise LookupError(f"EIA route /{ROUTE} lacks the duoarea or product facet.")
    region = pick_facet(client.get(f"{ROUTE}/facet/duoarea")["response"]["facets"], config.get(cfg, "fetch.eia.region_description"))
    product = pick_facet(client.get(f"{ROUTE}/facet/product")["response"]["facets"], config.get(cfg, "fetch.eia.product_description"))
    return {"duoarea": region["id"], "product": product["id"], "route_name": meta.get("name", ""),
            "area_name": region["name"], "product_name": product["name"]}


def fetch_rows(client: EiaClient, cfg: dict, duoarea: str, product: str) -> list[dict]:
    """Download every weekly row for the chosen region and product, page by page."""
    page = int(config.get(cfg, "fetch.eia.page_size"))
    params = {
        "frequency": "weekly",
        "data[0]": "value",
        "facets[duoarea][]": duoarea,
        "facets[product][]": product,
        "start": str(config.get(cfg, "fetch.eia.start_date")),
        "sort[0][column]": "period",
        "sort[0][direction]": "asc",
        "length": page,
    }
    rows: list[dict] = []
    offset = 0
    while True:
        body = client.get(f"{ROUTE}/data", {**params, "offset": offset})["response"]
        batch = body.get("data", [])
        rows.extend(batch)
        offset += page
        if not batch or offset >= int(body.get("total", 0)):
            return rows


def single_series(rows: list[dict]) -> dict:
    """Confirm the rows all belong to one series and return its ID, description and units."""
    series = {r["series"] for r in rows}
    if len(series) != 1:
        raise LookupError(f"Expected one EIA series for the chosen region and product, found {sorted(series)}.")
    first = rows[0]
    return {"series_id": first["series"], "description": first.get("series-description", ""), "units": first.get("units", "")}


def confirm_series_listed(client: EiaClient, series_id: str) -> None:
    """Cross-check the series ID against the API's own `series` facet list."""
    listed = {f["id"] for f in client.get(f"{ROUTE}/facet/series")["response"]["facets"]}
    if series_id not in listed:
        raise LookupError(f"Series {series_id} is not in the EIA series facet list.")


def to_price_rows(rows: list[dict], series_id: str) -> list[dict]:
    """Turn raw API rows into contract rows: one per week, ascending, price rounded to 3 decimals."""
    by_week: dict[str, float] = {}
    for r in rows:
        if r.get("value") in (None, ""):
            continue
        by_week[r["period"]] = round(float(r["value"]), 3)
    return [{"week_start": wk, "price_per_gal": by_week[wk], "series_id": series_id} for wk in sorted(by_week)]


def render_csv(price_rows: list[dict]) -> str:
    """CSV text with the contract's columns, `\\n` line endings and 3-decimal prices."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for r in price_rows:
        writer.writerow([r["week_start"], f"{r['price_per_gal']:.3f}", r["series_id"]])
    return buf.getvalue()


def fetch_eia(cfg: dict, *, refresh: bool, raw_dir: Path, cache_dir: Path) -> dict:
    """Fetch weekly diesel prices (cache first), write series_meta.json and eia_diesel.csv."""
    client = EiaClient(cfg, cache_dir, refresh)
    found = discover_facets(client, cfg)
    rows = fetch_rows(client, cfg, found["duoarea"], found["product"])
    if not rows:
        raise LookupError("EIA returned no weekly rows for the chosen series.")
    info = single_series(rows)
    confirm_series_listed(client, info["series_id"])
    price_rows = to_price_rows(rows, info["series_id"])

    meta_path = cache_dir / "series_meta.json"
    old = cache.read_json(meta_path) or {}
    meta = {
        "route": ROUTE,
        "facets": {"frequency": "weekly", "duoarea": found["duoarea"], "product": found["product"]},
        "series_id": info["series_id"],
        "description": info["description"],
        "units": info["units"],
        "fetched_on": (None if client.network_calls else old.get("fetched_on")) or dt.date.today().isoformat(),
    }
    cache.write_json(meta_path, meta)
    cache.write_text(raw_dir / "eia_diesel.csv", render_csv(price_rows))
    return {"rows": len(price_rows), "first": price_rows[0]["week_start"], "last": price_rows[-1]["week_start"],
            "series_id": info["series_id"], "network_calls": client.network_calls}

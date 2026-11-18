"""Fetch real public inputs (EIA diesel prices, OSM stations, anonymized customer points), cache first.

Owner: data-fetcher (Wave 1). Entry point: `run(cfg, refresh=False)` (docs/contracts.md section 8).
Each step reads data/cache/ first and calls the network only on a cache miss or `refresh=True`.
"""

from __future__ import annotations

from pathlib import Path

from fleetfuel import config
from fleetfuel.fetch import eia, osm


def run(cfg: dict, refresh: bool = False, *, raw_dir: Path | None = None, cache_dir: Path | None = None) -> None:
    """Write data/raw/eia_diesel.csv, stations.geojson and customer_points.csv.

    OSM steps run first so a missing EIA key never blocks them; the missing key is then
    reported as one plain-English error at the end. `raw_dir` and `cache_dir` exist so tests
    can point at temporary folders.
    """
    raw = raw_dir or config.RAW_DIR
    cache_root = cache_dir or config.CACHE_DIR
    session = osm.OverpassSession(cfg)

    st = osm.fetch_stations(cfg, session, refresh=refresh, raw_dir=raw, cache_dir=cache_root / "osm")
    print(f"[fetch] stations: {st['stations']} truck-capable ({st['deduped']} double-mapped entries merged)")
    if st["name_conflicts"]:
        print(f"[fetch] {len(st['name_conflicts'])} stations dropped for a name conflict: {', '.join(st['name_conflicts'])}")
    cu = osm.fetch_customers(cfg, session, refresh=refresh, raw_dir=raw, cache_dir=cache_root / "osm")
    print(f"[fetch] customers: {cu['written']} anonymous points (of {cu['available']} found)")

    try:
        ei = eia.fetch_eia(cfg, refresh=refresh, raw_dir=raw, cache_dir=cache_root / "eia")
    except eia.MissingKeyError as exc:
        print("[fetch] EIA diesel prices skipped: no key and no cache")
        raise RuntimeError(str(exc)) from None
    print(f"[fetch] EIA {ei['series_id']}: {ei['rows']} weeks, {ei['first']} to {ei['last']}")

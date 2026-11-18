"""Truck routing with openrouteservice: road geometry for each route and route x station detours.

In business terms, this step answers two questions the savings math needs. How long is each
route on real truck roads (and what does it look like on a map)? And for every route and every
candidate fuel station, what is the cheapest place to slot a fuel stop in, and how many extra
miles and minutes does that cost? The second answer is `ref_detour`, so savings can always be
reported net of the detour (SPEC section 6).

Public functions:
    run(cfg, ...)                    build step: fills dim_route geometry and ref_detour (contract 8)
    route_geometry(cfg, route_id)    road path of one route as a GeoJSON LineString
    detour_path(cfg, route_id, station_id, insert_after_stop)   road path with a station inserted
    detour_leg_path(cfg, route_id, station_id, insert_after_stop)   road path of just the fuel-stop leg
    refresh_cache(cfg, prune=...)    one-off: rebuild routes and detours, warm every leg, optionally prune unused cache files
    warm_detour_leg_cache(cfg, ...)  one-off cache warm-up for every ref_detour row (never part of the build)
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from fleetfuel import config, db, stations
from fleetfuel.routing import detours
from fleetfuel.routing.client import OrsClient, RoutableError
from fleetfuel.routing.routes import load_routes, stop_minutes

DQ_CHECK = "routing_detours"


def _coords(stops: list[dict]) -> list[tuple[float, float]]:
    return [(float(s["lat"]), float(s["lon"])) for s in stops]


def reachable_points(cfg: dict, points: list[tuple[float, float]], *, cache_dir: Path | None = None,
                     client: OrsClient | None = None) -> list[bool]:
    """True for each (lat, lon) point that openrouteservice can place on a truck road within
    `routing.reachable_radius_m`, in input order. Used to reject customer points inside, say, an air base."""
    if not points:
        return []
    client = client or OrsClient(cfg, cache_dir)
    return client.snap([(float(a), float(b)) for a, b in points], float(config.get(cfg, "routing.reachable_radius_m")))


def route_geometry(cfg: dict, route_id: str, *, db_path: Path | None = None, raw_dir: Path | None = None,
                   cache_dir: Path | None = None, client: OrsClient | None = None) -> dict:
    """Road path of one route (through all its stops, in order) as a GeoJSON LineString. Cached."""
    routes = load_routes(db_path, raw_dir)
    if route_id not in routes:
        raise KeyError(f"Route {route_id} is not in dim_route or the route plans.")
    client = client or OrsClient(cfg, cache_dir)
    return client.directions(_coords(routes[route_id]["stops"]))["geometry"]


def detour_path(cfg: dict, route_id: str, station_id: str, insert_after_stop: int, *, db_path: Path | None = None,
                raw_dir: Path | None = None, cache_dir: Path | None = None, client: OrsClient | None = None) -> dict:
    """Road path of the route with the station added after stop `insert_after_stop` (a StopSeq), as a GeoJSON LineString.

    One directions call per distinct route-station-gap, then cached.
    """
    routes = load_routes(db_path, raw_dir)
    if route_id not in routes:
        raise KeyError(f"Route {route_id} is not in dim_route or the route plans.")
    frame, _ = stations.load_stations(raw_dir)
    match = frame[frame["station_id"] == station_id]
    if match.empty:
        raise KeyError(f"Station {station_id} is not in the station list.")
    st = match.iloc[0]
    stops = routes[route_id]["stops"]
    split = [i for i, s in enumerate(stops) if int(s["seq"]) == int(insert_after_stop)]
    if not split:
        raise KeyError(f"Route {route_id} has no stop {insert_after_stop}.")
    coords = _coords(stops)
    coords.insert(split[0] + 1, (float(st["lat"]), float(st["lon"])))
    client = client or OrsClient(cfg, cache_dir)
    return client.directions(coords)["geometry"]


def detour_leg_path(cfg: dict, route_id: str, station_id: str, insert_after_stop: int, *, db_path: Path | None = None,
                    raw_dir: Path | None = None, cache_dir: Path | None = None, client: OrsClient | None = None) -> dict:
    """Road path of just the fuel-stop leg: from stop `insert_after_stop` (a StopSeq) to the station to the next stop.

    This is what the site draws for a detour. One directions call (three waypoints), then cached, so a
    repeat costs nothing. Unknown route, station or stop (or no next stop) raises KeyError.
    """
    routes = load_routes(db_path, raw_dir)
    if route_id not in routes:
        raise KeyError(f"Route {route_id} is not in dim_route or the route plans.")
    frame, _ = stations.load_stations(raw_dir)
    match = frame[frame["station_id"] == station_id]
    if match.empty:
        raise KeyError(f"Station {station_id} is not in the station list.")
    st = match.iloc[0]
    stops = routes[route_id]["stops"]
    here = [i for i, s in enumerate(stops) if int(s["seq"]) == int(insert_after_stop)]
    if not here:
        raise KeyError(f"Route {route_id} has no stop {insert_after_stop}.")
    if here[0] + 1 >= len(stops):
        raise KeyError(f"Route {route_id} has no stop after stop {insert_after_stop}, so no fuel-stop leg.")
    before, after = stops[here[0]], stops[here[0] + 1]
    coords = [(float(before["lat"]), float(before["lon"])), (float(st["lat"]), float(st["lon"])),
              (float(after["lat"]), float(after["lon"]))]
    client = client or OrsClient(cfg, cache_dir)
    return client.directions(coords)["geometry"]


def warm_detour_leg_cache(cfg: dict, *, db_path: Path | None = None, raw_dir: Path | None = None,
                          cache_dir: Path | None = None, client: OrsClient | None = None) -> dict:
    """One-off: save the road path of every ref_detour row to the cache, so the site build needs no key.

    Counts live and cached calls, pauses between live calls (the client does this) and stops cleanly
    before the daily directions limit; run it again tomorrow to finish. Not part of `run` or the build.
    Returns {"rows", "live", "cached", "failed": [(route, station, stop, reason)],
    "unroutable": [same shape: refused even with the wider radius, cached, not retried], "stopped_early"}.
    """
    db_file = Path(db_path or config.DB_PATH)
    con = db.connect(db_file)
    try:
        rows = con.execute(
            "SELECT DISTINCT route_id, station_id, insert_after_stop FROM ref_detour ORDER BY 1, 2, 3"
        ).fetchall()
    finally:
        con.close()
    client = client or OrsClient(cfg, cache_dir)
    limit = client.limits["directions"]
    failed: list[tuple] = []
    unroutable: list[tuple] = []
    stopped_early = False
    for route_id, station_id, stop in rows:
        if client.live["directions"] >= limit:
            stopped_early = True
            print(f"[routing] stopping before the free-plan limit of {limit} directions calls; run again tomorrow.")
            break
        try:
            detour_leg_path(cfg, route_id, station_id, int(stop), db_path=db_file, raw_dir=raw_dir, client=client)
        except RoutableError as exc:
            # Refused even with the wider search radius (and remembered in the cache): not a failure to retry.
            unroutable.append((route_id, station_id, int(stop), str(exc)[:200]))
        except KeyError as exc:
            failed.append((route_id, station_id, int(stop), str(exc)))
        except RuntimeError as exc:
            # Still rate limited after every try, or unreachable: count it and move on. A rerun is resumable,
            # because every leg already saved is read from the cache and only the missing ones are fetched.
            failed.append((route_id, station_id, int(stop), str(exc).splitlines()[0][:200]))
    print(f"[routing] detour legs: {len(rows)} rows, {len(failed)} failed, {len(unroutable)} unroutable; {client.summary()}")
    return {"rows": len(rows), "live": client.live["directions"], "cached": client.cached["directions"],
            "failed": failed, "unroutable": unroutable, "stopped_early": stopped_early}


def _route_detours(cfg: dict, client: OrsClient, route_id: str, stops: list[dict], geometry: dict, frame: pd.DataFrame) -> tuple[list[dict], int]:
    """(ref_detour rows, unroutable pairs) for one route: every station within the candidate radius, cheapest gap each."""
    radius = float(config.get(cfg, "routing.candidate_station_radius_mi"))
    line = geometry["coordinates"]
    near = frame[[detours.miles_from_line(r.lat, r.lon, line) <= radius for r in frame.itertuples(index=False)]]
    points, order = detours.unique_points(stops)
    size = detours.chunk_size(len(points), client.max_cells)
    rows: list[dict] = []
    skipped = 0
    ordered = near.sort_values("station_id")
    for start in range(0, len(ordered), size):
        chunk = ordered.iloc[start:start + size]
        coords = points + [(float(r.lat), float(r.lon)) for r in chunk.itertuples(index=False)]
        grid = client.matrix(coords)
        for offset, r in enumerate(chunk.itertuples(index=False)):
            found = detours.best_insertion(order, len(points) + offset, grid["meters"], grid["seconds"])
            if found is None:
                skipped += 1
                continue
            gap, miles, minutes = found
            rows.append({
                "route_id": route_id, "station_id": r.station_id, "insert_after_stop": int(stops[gap]["seq"]),
                "extra_miles": miles, "extra_minutes": minutes,
            })
    return rows, skipped


def _write(db_path: Path, route_rows: list[dict], detour_rows: list[dict], skipped: int) -> None:
    """Store the road figures in dim_route and replace ref_detour (so a rebuild is idempotent)."""
    con = db.connect(db_path)
    try:
        for r in route_rows:
            con.execute(
                "UPDATE dim_route SET base_miles = ?, base_minutes = ?, geometry_geojson = ? WHERE route_id = ?",
                [r["base_miles"], r["base_minutes"], r["geometry_geojson"], r["route_id"]],
            )
        con.execute("DELETE FROM ref_detour")
        db.insert_frame(con, "ref_detour", pd.DataFrame(detour_rows))
        con.execute("DELETE FROM dq_log WHERE check_name = ?", [DQ_CHECK])
        db.log_dq(
            con, DQ_CHECK, len(route_rows), len(detour_rows), skipped,
            f"Road routes for {len(route_rows)} routes and {len(detour_rows)} route-station detours; "
            f"{skipped} pairs could not be routed on truck roads.",
        )
    finally:
        con.close()


def run(cfg: dict, *, db_path: Path | None = None, raw_dir: Path | None = None, cache_dir: Path | None = None,
        client: OrsClient | None = None) -> None:
    """Build step (contract 8): road geometry into dim_route and every route x station detour into ref_detour."""
    db_file = Path(db_path or config.DB_PATH)
    if not db_file.is_file():
        raise FileNotFoundError(f"{db_file} not found. Run the ingest and normalize steps first (`uv run fleetfuel build`).")
    routes = load_routes(db_file, raw_dir)
    frame, _ = stations.load_stations(raw_dir)
    client = client or OrsClient(cfg, cache_dir)
    route_rows: list[dict] = []
    detour_rows: list[dict] = []
    skipped = 0
    for route_id, route in routes.items():
        stops = route["stops"]
        road = client.directions(_coords(stops))
        road_min = road["seconds"] / detours.SECONDS_PER_MINUTE
        route_rows.append({
            "route_id": route_id,
            "base_miles": road["meters"] / detours.METERS_PER_MILE,
            "base_minutes": road_min + stop_minutes(cfg, stops),
            "geometry_geojson": json.dumps(road["geometry"], separators=(",", ":")),
        })
        rows, bad = _route_detours(cfg, client, route_id, stops, road["geometry"], frame)
        detour_rows += rows
        skipped += bad
        print(f"[routing] {route_id}: {route_rows[-1]['base_miles']:.1f} road miles")
    candidates = len(routes) * len(frame)
    _write(db_file, route_rows, detour_rows, skipped=skipped)
    print(f"[routing] ref_detour rows: {len(detour_rows)} (of {candidates} route-station pairs; the rest are beyond the candidate radius)")
    print(f"[routing] {client.summary()}")


def refresh_cache(cfg: dict, *, prune: bool = False, db_path: Path | None = None, raw_dir: Path | None = None,
                  cache_dir: Path | None = None, client: OrsClient | None = None) -> dict:
    """One-off (contract 14.2, never part of the build): redo routes and ref_detour, warm every detour leg, optionally prune.

    Runs in one process so the client sees every cache file the current plans need. With prune=True it
    then deletes every cache file nobody used (answers for old coordinates), unless the daily limit
    stopped the warm-up early, since then the unused files may simply be ones not reached yet.
    Unroutable legs (refused even with the wider radius, and cached) do not block pruning; failed ones do.
    Returns {live_directions, live_matrix, cache_hits, failed, unroutable, pruned}.
    """
    client = client or OrsClient(cfg, cache_dir)
    run(cfg, db_path=db_path, raw_dir=raw_dir, cache_dir=cache_dir, client=client)
    warm = warm_detour_leg_cache(cfg, db_path=db_path, raw_dir=raw_dir, client=client)
    removed = 0
    if prune and warm["stopped_early"]:
        print("[routing] not pruning: the daily limit stopped the warm-up early. Run again tomorrow, then prune.")
    elif prune and warm["failed"]:
        print(f"[routing] not pruning: {len(warm['failed'])} legs failed. Run refresh_cache again (it only fetches what is missing), then prune.")
    elif prune:
        for file in sorted(client.cache_dir.glob("*.json")):
            if file not in client.used:
                file.unlink()
                removed += 1
    result = {"live_directions": client.live["directions"], "live_matrix": client.live["matrix"],
              "cache_hits": sum(client.cached.values()), "failed": len(warm["failed"]),
              "unroutable": len(warm["unroutable"]), "pruned": removed}
    print(f"[routing] refresh_cache: {result}")
    return result

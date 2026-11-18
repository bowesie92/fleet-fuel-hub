"""Write the small JSON and GeoJSON files the static site reads (docs/contracts.md section 7).

In business terms: the site has no server, so every number a page shows is precomputed here and
saved as a small file in site/data/. This module takes the analytics results and the clean hub
and formats them exactly as the contract says: money to the cent, prices to the tenth of a cent,
customers only as "Customer 014", and a SYNTHETIC flag in every file.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from fleetfuel import config
from fleetfuel.analytics import has_yard_table, read_results, tank_yards
from fleetfuel.export.stamp import stamp_site
from fleetfuel.stations import display_name, load_stations

SAMPLE_ROWS = 5
PATH_DECIMALS = 5  # contract 11.6: detour path coordinates
STALE_PATH_FILE = "missed_paths.geojson"  # replaced by detour_paths.geojson in contract v1.11
SCOUT_SCOPE = "Tucson box, truck-confirmed stations that are not partners"
YARD_SOURCE = ("Phoenix DC tank log", "yard_fuel.csv", "Not a card feed: these are on-site fills at the Phoenix DC tank, kept apart from fuel card transactions.")
SOURCES = [
    ("Fuel card export", "fuel_card.csv"),
    ("GPS telematics export", "telematics.csv"),
    ("Route plan export", "route_plans.csv"),
]


# ---------------------------------------------------------------- small helpers

def write_json(path: Path, payload: dict) -> None:
    """Write one site file: UTF-8, one-space indent, newline at the end. NaN is refused so the site never sees it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=1, ensure_ascii=False, allow_nan=False)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def clean(value):
    """A pandas or numpy value as a plain JSON value (NaN and NaT become None)."""
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp,)):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        return value.item()
    return value


def customer_label(location_id: str) -> str:
    """CUST-0014 becomes 'Customer 014' (the last three digits of the number, zero-padded)."""
    number = int(re.sub(r"\D", "", location_id))
    return f"Customer {number % 1000:03d}"


def route_label(route_id: str) -> str:
    """Contract section 9: 'Route R01' for local routes, 'Phoenix to Tucson linehaul, run 1' for linehaul."""
    prefix, code = route_id.split("-", 1)
    if prefix == "PHX":
        return f"Phoenix to Tucson linehaul, run {int(code.lstrip('L'))}"
    return f"Route {code}"


# ---------------------------------------------------------------- one writer per file

DERIVED_PREFIX = "derived."
TUCSON_MILES_NOTE = "synthetic-route assumption: openrouteservice road miles over the synthetic route plans"


def derived_assumption(key: str, con: duckdb.DuckDBPyConnection, cfg: dict) -> dict:
    """A headline entry computed from the hub (contract 7, v1.7). An unknown derived key is an error."""
    if key != "derived.tucson_local_route_miles_per_day":
        raise KeyError(f"site.headline_assumption_keys lists an unknown derived key: {key}")
    miles = [r[0] for r in con.execute("SELECT base_miles FROM dim_route WHERE route_id LIKE 'TUC-R%' AND base_miles IS NOT NULL").fetchall()]
    if not miles:
        raise ValueError(f"{key} needs TUC-R.. routes with base_miles in dim_route, but there are none.")
    return {"key": key, "value": round(sum(miles) / len(miles), 1), "note": TUCSON_MILES_NOTE, "source": "derived"}


def headline_assumptions(cfg: dict, assumptions: list[dict], con: duckdb.DuckDBPyConnection) -> list[dict]:
    """The entries named in site.headline_assumption_keys, in that order, each tagged config or derived."""
    by_key = {a["key"]: a for a in assumptions}
    out = []
    for key in config.get(cfg, "site.headline_assumption_keys"):
        if key.startswith(DERIVED_PREFIX):
            out.append(derived_assumption(key, con, cfg))
        elif key in by_key:
            out.append({**by_key[key], "source": "config"})
        else:
            raise KeyError(f"site.headline_assumption_keys lists keys that are not in config/assumptions.yaml: {key}")
    return out


def write_meta(out: Path, cfg: dict, generated_on: str, trucks: int, con: duckdb.DuckDBPyConnection) -> None:
    """meta.json: banner flag, period, every assumption with its note, the fleet size and the headline assumptions."""
    assumptions = config.describe_assumptions()
    write_json(out / "meta.json", {
        "synthetic": True, "generated_on": generated_on,
        "period_start": str(config.get(cfg, "period.start_date")), "period_end": str(config.get(cfg, "period.end_date")),
        "assumptions": assumptions, "trucks_in_fleet": trucks,
        "headline_assumptions": headline_assumptions(cfg, assumptions, con),
    })


def write_overview(out: Path, results: dict) -> None:
    """overview.json: the KPI tiles (card and yard gallons split; missed savings is the confirmed-only total)."""
    write_json(out / "overview.json", {"synthetic": True, "kpis": results["kpis"], "hos_check": results["hos_check"]})


def station_frame(con: duckdb.DuckDBPyConnection, raw_dir: Path | None) -> pd.DataFrame:
    """dim_station plus the city from the shared station loader (city stays empty if the loader cannot run)."""
    stations = con.execute("SELECT * FROM dim_station ORDER BY station_id").df()
    try:
        cities = load_stations(raw_dir)[0][["station_id", "city"]]
        stations = stations.merge(cities, on="station_id", how="left")
    except (FileNotFoundError, ValueError):
        stations["city"] = None
    stations["display_name"] = [display_name(r) for r in stations.to_dict("records")]
    return stations


def write_stations(out: Path, stations: pd.DataFrame, results: dict) -> None:
    """stations.geojson: every station with its fill totals, average prices and break-even gap."""
    stats = results["station_stats"]
    features = []
    for r in stations.to_dict("records"):
        s = stats.get(r["station_id"], {})
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(float(r["lon"]), 6), round(float(r["lat"]), 6)]},
            "properties": {
                "station_id": r["station_id"], "name": clean(r["name"]), "display_name": r["display_name"],
                "city": clean(r["city"]), "brand": clean(r["brand"]), "station_class": r["station_class"],
                "truck_access": r["truck_access"], "is_partner": bool(r["is_partner"]),
                "partner_discount_cpg": float(r["partner_discount_cpg"]),
                "fills": s.get("fills", 0), "gallons": s.get("gallons", 0.0),
                "avg_price_paid": s.get("avg_price_paid"), "avg_posted_price": s.get("avg_posted_price"),
                "break_even_cpg": s.get("break_even_cpg"), "break_even_route_id": s.get("break_even_route_id"),
                "break_even_is_peak": s.get("break_even_is_peak"),
            },
        })
    write_json(out / "stations.geojson", {"type": "FeatureCollection", "synthetic": True, "features": features})


def write_price_history(out: Path, results: dict) -> None:
    """price_history.json: EIA weekly series plus each station's weekly price paid and posted."""
    write_json(out / "price_history.json", {"synthetic": True, **results["price_history"]})


def route_features(con: duckdb.DuckDBPyConnection, cfg: dict) -> list[dict]:
    """routes.geojson features: one LineString per route plus a Point per stop, with anonymized labels."""
    yard_labels = {config.get(cfg, f"yards.{k}.id"): config.get(cfg, f"yards.{k}.label") for k in ("phoenix_dc", "tucson_yard")}
    features = []
    for r in con.execute("SELECT * FROM dim_route ORDER BY route_id").df().to_dict("records"):
        stops = json.loads(r["stops_json"])
        geometry = json.loads(r["geometry_geojson"]) if r["geometry_geojson"] else {
            "type": "LineString", "coordinates": [[s["lon"], s["lat"]] for s in stops]}
        if geometry.get("type") == "Feature":
            geometry = geometry["geometry"]
        features.append({"type": "Feature", "geometry": geometry, "properties": {
            "kind": "route", "route_id": r["route_id"], "label": route_label(r["route_id"]), "yard": r["yard"],
            "base_miles": clean(r["base_miles"]), "base_minutes": clean(r["base_minutes"]), "stop_count": len(stops)}})
        for s in stops:
            label = customer_label(s["location_id"]) if s["location_id"].startswith("CUST-") else yard_labels.get(s["location_id"], s["location_id"])
            features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [s["lon"], s["lat"]]},
                             "properties": {"kind": "stop", "route_id": r["route_id"], "seq": int(s["seq"]), "label": label}})
    return features


def write_routes(out: Path, con: duckdb.DuckDBPyConnection, cfg: dict) -> None:
    """routes.geojson."""
    write_json(out / "routes.geojson", {"type": "FeatureCollection", "synthetic": True, "features": route_features(con, cfg)})


def scout_method(cfg: dict) -> str:
    """The scout's method text: only what is counted (fuel burned, driver time, wear, the detour limit), no schedule risk."""
    limit = float(config.get(cfg, "time_cost.max_detour_minutes"))
    return (
        "Fills already made at a station save the discount on every gallon. Fills made elsewhere move to it only when, "
        "after the fuel burned, the driver time and the wear of the extra driving to reach it, the stop is cheaper by more than "
        f"the minimum saving, and the detour is no longer than {limit:g} minutes (rush-hour minutes count extra). "
        "The deal is credited only with the savings it creates, at most the discount on the gallons it captures."
    )


def write_detours(out: Path, results: dict) -> None:
    """detours.json (Test route page), with the truck access toggle fields."""
    write_json(out / "detours.json", {"synthetic": True, **results["detours"]})


def public_rows(rows: list[dict]) -> list[dict]:
    """Missed rows as the contract lists them, numbers rounded for display."""
    out = []
    for r in rows:
        row = dict(r)
        row["gallons"] = round(row["gallons"], 3)
        row["actual_price_per_gal"] = round(row["actual_price_per_gal"], 3)
        row["alt_price_per_gal"] = round(row["alt_price_per_gal"], 3)
        row["actual_extra_miles"] = round(row["actual_extra_miles"], 2)
        row["actual_extra_minutes"] = round(row["actual_extra_minutes"], 1)
        row["alt_extra_miles"] = round(row["alt_extra_miles"], 2)
        row["alt_extra_minutes"] = round(row["alt_extra_minutes"], 1)
        out.append(row)
    return out


def write_missed(out: Path, results: dict) -> None:
    """missed.json: confirmed-only numbers as the default, plus the *_incl_unverified versions for the toggle."""
    base, incl = results["missed"], results["missed_incl_unverified"]
    write_json(out / "missed.json", {
        "synthetic": True,
        "trucks_in_fleet": results["notes"]["trucks_in_fleet"],
        "usd_per_truck_per_year": base["usd_per_truck_per_year"],
        "usd_per_truck_per_year_incl_unverified": incl["usd_per_truck_per_year"],
        "card_gallons": base["card_gallons"], "cpg_per_card_gallon": base["cpg_per_card_gallon"],
        "cpg_per_card_gallon_incl_unverified": incl["cpg_per_card_gallon"],
        "total_usd": base["total_usd"], "fill_count": base["fill_count"], "weekly": base["weekly"], "rows": public_rows(base["rows"]),
        "total_usd_incl_unverified": incl["total_usd"], "fill_count_incl_unverified": incl["fill_count"],
        "weekly_incl_unverified": incl["weekly"], "rows_incl_unverified": public_rows(incl["rows"]),
    })


def path_coordinates(path) -> list | None:
    """Pull [lon, lat] pairs (5 decimals) out of whatever the path function returns (a GeoJSON geometry, a Feature or a bare list)."""
    if isinstance(path, dict):
        path = path.get("geometry", path).get("coordinates")
    if not path or len(path) < 2:
        return None
    return [[round(float(p[0]), PATH_DECIMALS), round(float(p[1]), PATH_DECIMALS)] for p in path]


def detour_keys(con: duckdb.DuckDBPyConnection, limit: int) -> list[tuple[str, str, int]]:
    """The first `limit` (route, station, insert-after-stop) rows of ref_detour, in a stable order."""
    rows = con.execute("SELECT route_id, station_id, insert_after_stop FROM ref_detour ORDER BY route_id, station_id, insert_after_stop LIMIT ?", [limit]).fetchall()
    return [(r[0], r[1], int(r[2])) for r in rows]


def write_detour_paths(out: Path, cfg: dict, con: duckdb.DuckDBPyConnection) -> int:
    """detour_paths.geojson: the road path of each route-station detour, from routing's detour_leg_path.

    One LineString per ref_detour row, up to export.max_detour_paths. Rows whose path is not available (routing lacks it,
    or raises) are left out and the site falls back to a straight line. The old missed_paths.geojson is deleted.
    Returns the number of paths written.
    """
    try:
        from fleetfuel import routing
        leg_path = getattr(routing, "detour_leg_path", None)
    except ImportError:
        leg_path = None
    features = []
    if leg_path is not None:
        for route_id, station_id, after in detour_keys(con, int(config.get(cfg, "export.max_detour_paths"))):
            try:
                coords = path_coordinates(leg_path(cfg, route_id, station_id, after))
            except Exception as err:  # routing may lack a cached path; the page falls back to a straight line
                print(f"[export] no road path for {route_id} {station_id} after stop {after}: {err}")
                continue
            if coords:
                features.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
                                 "properties": {"route_id": route_id, "station_id": station_id, "insert_after_stop": after}})
    write_json(out / "detour_paths.geojson", {"type": "FeatureCollection", "synthetic": True, "features": features})
    stale = out / STALE_PATH_FILE
    if stale.exists():
        stale.unlink()  # it pointed at old transactions and is no longer read
    return len(features)


def write_partner_scout(out: Path, cfg: dict, stations: pd.DataFrame, results: dict) -> None:
    """partner_scout.json: the Overview lead insight, ranked by annual net savings at the default discount."""
    by_id = {r["station_id"]: r for r in stations.to_dict("records")}
    steps = [p["discount_cpg"] for p in results["partner_scout"][0]["curve"]] if results["partner_scout"] else []
    rows = [{"station_id": e["station_id"], "display_name": by_id[e["station_id"]]["display_name"],
             "lat": round(float(by_id[e["station_id"]]["lat"]), 6), "lon": round(float(by_id[e["station_id"]]["lon"]), 6),
             "annual_savings_without_deal_usd": e["annual_savings_without_deal_usd"], "curve": e["curve"]} for e in results["partner_scout"]]
    write_json(out / "partner_scout.json", {
        "synthetic": True, "scenario_label": "Synthetic scenario", "scope": SCOUT_SCOPE,
        "trucks_in_fleet": results["notes"]["trucks_in_fleet"],
        "discount_steps_cpg": steps, "default_discount_cpg": int(config.get(cfg, "partner_scout.default_discount_cpg")),
        "best_station_id_at_default": rows[0]["station_id"] if rows else None, "method": scout_method(cfg), "stations": rows,
    })


def sample_records(frame: pd.DataFrame) -> list[dict]:
    """The first few rows as plain dictionaries."""
    return [{k: clean(v) for k, v in rec.items()} for rec in frame.head(SAMPLE_ROWS).to_dict("records")]


def write_hub(out: Path, con: duckdb.DuckDBPyConnection, raw_dir: Path | None, cfg: dict) -> None:
    """hub.json (Data hub page): the three raw feeds, the clean table, the quality checks and the accuracy scores."""
    raw = Path(raw_dir or config.RAW_DIR)
    sources = []
    for name, file in SOURCES:
        frame = pd.read_csv(raw / file, dtype=str, keep_default_na=False)
        frame = frame.replace("", None)
        sources.append({"name": name, "file": file, "rows": int(len(frame)), "columns": list(frame.columns), "sample": sample_records(frame)})
    yard_listed = has_yard_table(con) and (raw / YARD_SOURCE[1]).is_file()
    if tank_yards(cfg) and not yard_listed:
        raise FileNotFoundError(
            f"{', '.join(tank_yards(cfg))} has an on-site fuel tank, so the Phoenix DC tank log must be a source on the Data hub, "
            f"but {YARD_SOURCE[1]} or fact_yard_fuel is missing."
        )
    if yard_listed:
        frame = pd.read_csv(raw / YARD_SOURCE[1], dtype=str, keep_default_na=False).replace("", None)
        sources.append({"name": YARD_SOURCE[0], "file": YARD_SOURCE[1], "rows": int(len(frame)), "columns": list(frame.columns),
                        "sample": sample_records(frame), "note": YARD_SOURCE[2]})
    clean_frame = con.execute("SELECT * FROM fact_fuel_txn ORDER BY ts_local, txn_id").df()
    dq = con.execute("SELECT check_name, rows_in, rows_out, issue_count, notes FROM dq_log").df()
    acc = con.execute("SELECT metric, value, notes FROM eval_accuracy").df()
    write_json(out / "hub.json", {
        "synthetic": True, "sources": sources,
        "clean_table": {"name": "fact_fuel_txn", "rows": int(len(clean_frame)), "columns": list(clean_frame.columns), "sample": sample_records(clean_frame)},
        "dq": [{k: clean(v) for k, v in r.items()} for r in dq.to_dict("records")],
        "accuracy": [{"metric": r["metric"], "value": round(float(r["value"]), 3), "notes": clean(r["notes"])} for r in acc.to_dict("records")],
    })


# ---------------------------------------------------------------- entry point

def run(cfg: dict, db_path: Path | str | None = None, out_dir: Path | str | None = None,
        raw_dir: Path | str | None = None, generated_on: str | None = None, **_: object) -> None:
    """Write every site data file from the hub and the stored analytics results."""
    path = Path(db_path or config.DB_PATH)
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found. Run `uv run fleetfuel build` first.")
    out = Path(out_dir or config.SITE_DATA_DIR)
    raw = Path(raw_dir) if raw_dir else None
    con = duckdb.connect(str(path), read_only=True)
    try:
        results = read_results(con)
        stations = station_frame(con, raw)
        write_meta(out, cfg, generated_on or date.today().isoformat(), results["notes"]["trucks_in_fleet"], con)
        write_overview(out, results)
        write_stations(out, stations, results)
        write_price_history(out, results)
        write_routes(out, con, cfg)
        write_detours(out, results)
        write_missed(out, results)
        drawn = write_detour_paths(out, cfg, con)
        write_partner_scout(out, cfg, stations, results)
        write_hub(out, con, raw, cfg)
    finally:
        con.close()
    version = stamp_site(out)
    print(f"[export] wrote site data to {out} (detour paths drawn: {drawn}; build version {version})")

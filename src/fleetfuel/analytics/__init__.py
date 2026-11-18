"""Analytics: price history, net savings, missed savings, break-even gaps and the Tucson partnership scout.

Step 4 of `uv run fleetfuel build` (docs/contracts.md section 8). It reads the clean hub
(and `ref_detour`, the precomputed extra miles and minutes for each route and station),
does every calculation, and stores the results as JSON in one hub table, `an_result`.
`export.run` then turns those results into the small files the site reads. Every input
comes from config/assumptions.yaml.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd

from fleetfuel import config
from fleetfuel.analytics import detour_options, partner_scout, price_history
from fleetfuel.analytics.missed import find_missed
from fleetfuel.analytics.net_savings import Inputs, SavingsParams, load_inputs

RESULT_TABLE = "an_result"
DAYS_PER_YEAR = 365  # a calendar fact, not an assumption
__all__ = ["run", "read_results", "compute_results"]


def _iso(value) -> str:
    """A date or timestamp as YYYY-MM-DD."""
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def weekly_missed(rows: list[dict], cfg: dict) -> list[dict]:
    """Missed savings per week (Monday start), including weeks with none, across the whole period."""
    totals = {_iso(w): [0.0, 0] for w in price_history.period_weeks(cfg)}
    for r in rows:
        key = _iso(price_history.week_start(pd.Timestamp(r["date"])))
        totals[key][0] += r["net_savings_usd"]
        totals[key][1] += 1
    return [{"week_start": k, "missed_savings_usd": round(v[0], 2), "fills": v[1]} for k, v in totals.items()]


def missed_block(rows: list[dict], cfg: dict) -> dict:
    """One version (confirmed only, or including unverified) of the missed-savings result. Total is the sum of the rounded rows."""
    clean = [{**r, "date": _iso(r["date"])} for r in rows]
    return {
        "total_usd": round(sum(r["net_savings_usd"] for r in rows), 2),
        "fill_count": len(rows),
        "weekly": weekly_missed(rows, cfg),
        "rows": clean,
    }


def tank_yards(cfg: dict) -> list[str]:
    """Labels of the yards that have an on-site fuel tank (yards.*.onsite_fuel is true)."""
    out = []
    for key in config.get(cfg, "yards"):
        if config.get(cfg, f"yards.{key}.onsite_fuel"):  # a missing key raises, naming it
            out.append(str(config.get(cfg, f"yards.{key}.label")))
    return out


def has_yard_table(con: duckdb.DuckDBPyConnection) -> bool:
    """True when the hub has the fact_yard_fuel table."""
    return bool(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'fact_yard_fuel'").fetchone()[0])


def yard_totals(con: duckdb.DuckDBPyConnection, cfg: dict) -> tuple[float, float]:
    """(gallons, dollars) of yard fills. Zero is allowed only when no yard has a tank; otherwise a missing or empty log is an error."""
    tanks = tank_yards(cfg)
    present = has_yard_table(con)
    count = con.execute("SELECT count(*) FROM fact_yard_fuel").fetchone()[0] if present else 0
    if tanks and count == 0:
        why = "is missing from the hub" if not present else "is empty"
        raise ValueError(
            f"{', '.join(tanks)} has an on-site fuel tank (yards.*.onsite_fuel) but fact_yard_fuel {why}. "
            "Run the ingest step with data/raw/yard_fuel.csv instead of reporting zero yard gallons."
        )
    if not present:
        return 0.0, 0.0
    gal, usd = con.execute("SELECT coalesce(sum(gallons), 0), coalesce(sum(gallons * price_per_gal), 0) FROM fact_yard_fuel").fetchone()
    return float(gal), float(usd)


def per_truck_per_year(period_total: float, period_days: int, trucks: int) -> float:
    """Contract 7: the period total scaled to 365 days, divided across the trucks in the fleet."""
    return round(period_total * DAYS_PER_YEAR / period_days / trucks, 2)


def trucks_in_fleet(con: duckdb.DuckDBPyConnection) -> int:
    """Number of trucks in dim_truck. An empty fleet is an error, since every per-truck figure divides by it."""
    n = int(con.execute("SELECT count(*) FROM dim_truck").fetchone()[0])
    if n == 0:
        raise ValueError("dim_truck is empty, so per-truck figures cannot be computed.")
    return n


def cents_per_gallon(usd: float, gallons: float) -> float | None:
    """Dollars spread over gallons, as cents per gallon with 2 decimals (None, shown as n/a, when there are no gallons)."""
    return round(usd / gallons * 100, 2) if gallons else None


def tucson_partner_share(con: duckdb.DuckDBPyConnection, cfg: dict) -> float | None:
    """Card gallons bought at partner stations by Tucson yard trucks, as a share of all Tucson yard card gallons (0 to 1; None when they bought no card gallons)."""
    yard = config.get(cfg, "yards.tucson_yard.id")
    row = con.execute(
        "SELECT sum(f.gallons), sum(CASE WHEN s.is_partner THEN f.gallons ELSE 0 END) "
        "FROM fact_fuel_txn f JOIN dim_truck t ON f.truck_id = t.truck_id "
        "LEFT JOIN dim_station s ON f.station_id = s.station_id WHERE t.home_yard = ?", [yard]).fetchone()
    total, partner = (float(v or 0.0) for v in row)
    return round(partner / total, 3) if total else None


def kpis(con: duckdb.DuckDBPyConnection, cfg: dict, confirmed_missed_usd: float, trucks: int, period_days: int) -> dict:
    """The Overview tiles. Card figures (average net price, partner share) cover fuel card fills only; yard fills are shown separately."""
    row = con.execute("SELECT sum(gallons), sum(gallons * price_per_gal) FROM fact_fuel_txn").fetchone()
    gallons, spend = (float(v or 0.0) for v in row)
    yard_gal, yard_usd = yard_totals(con, cfg)
    return {
        "total_gallons": int(round(gallons + yard_gal)),
        "card_gallons": int(round(gallons)),
        "yard_gallons": int(round(yard_gal)),
        "avg_net_price_per_gal": round(spend / gallons, 3) if gallons else 0.0,
        "avg_yard_price_per_gal": round(yard_usd / yard_gal, 3) if yard_gal else 0.0,
        "missed_savings_usd": round(confirmed_missed_usd, 2),
        "missed_usd_per_truck_per_year": per_truck_per_year(confirmed_missed_usd, period_days, trucks),
        "missed_savings_cpg_card": cents_per_gallon(confirmed_missed_usd, gallons),
        "tucson_card_gallons_partner_share": tucson_partner_share(con, cfg),
    }


def price_history_block(con: duckdb.DuckDBPyConnection, cfg: dict) -> dict:
    """EIA weekly series and per-station weekly price paid vs posted, in the contract 7 shape."""
    eia = price_history.eia_weekly(con, cfg)
    weekly = price_history.station_weekly(con, cfg)
    stations: dict[str, list[dict]] = {}
    for r in weekly.itertuples(index=False):
        stations.setdefault(r.station_id, []).append({
            "week_start": _iso(r.week_start),
            "avg_price_paid": None if pd.isna(r.avg_price_paid) else round(float(r.avg_price_paid), 3),
            "avg_posted_price": None if pd.isna(r.avg_posted_price) else round(float(r.avg_posted_price), 3),
            "gallons": round(float(r.gallons), 1),
        })
    return {
        "eia": [{"week_start": _iso(r.week_start), "price_per_gal": round(float(r.price_per_gal), 3)} for r in eia.itertuples(index=False)],
        "stations": stations,
    }


def station_stats_block(con: duckdb.DuckDBPyConnection, break_even: dict[str, dict]) -> dict:
    """Per-station totals for the map: fills, gallons, average paid and posted price, break-even gap."""
    out = {}
    for r in price_history.station_totals(con).itertuples(index=False):
        out[r.station_id] = {
            "fills": int(r.fills), "gallons": round(float(r.gallons), 1),
            "avg_price_paid": None if pd.isna(r.avg_price_paid) else round(float(r.avg_price_paid), 3),
            "avg_posted_price": None if pd.isna(r.avg_posted_price) else round(float(r.avg_posted_price), 3),
            "break_even_cpg": (break_even.get(r.station_id) or {}).get("cpg"),
            "break_even_route_id": (break_even.get(r.station_id) or {}).get("route_id"),
            "break_even_is_peak": (break_even.get(r.station_id) or {}).get("is_peak"),
        }
    return out


def hos_check(con: duckdb.DuckDBPyConnection, cfg: dict) -> dict:
    """Hours-of-service check (SPEC section 6): every route day with the longest allowed fuel stop added.

    Driving = the route's road minutes (base_minutes less unloading at each customer and the yard dwell at
    each middle yard stop) plus the maximum detour. On duty = base_minutes plus the maximum detour plus one fill.
    """
    per_stop = float(config.get(cfg, "routes.minutes_per_stop"))
    dwell = float(config.get(cfg, "routes.linehaul_yard_dwell_minutes"))
    detour = float(config.get(cfg, "time_cost.max_detour_minutes"))
    fill = float(config.get(cfg, "fueling.fill_minutes"))
    limit_drive = float(config.get(cfg, "hos.max_driving_hours"))
    limit_duty = float(config.get(cfg, "hos.max_on_duty_hours"))
    worst_drive = worst_duty = 0.0
    over = []
    for route_id, base, stops_json in con.execute("SELECT route_id, base_minutes, stops_json FROM dim_route ORDER BY route_id").fetchall():
        stops = json.loads(stops_json)
        customers = sum(1 for st in stops if str(st["location_id"]).startswith("CUST-"))
        middle_yards = sum(1 for i, st in enumerate(stops) if 0 < i < len(stops) - 1 and not str(st["location_id"]).startswith("CUST-"))
        driving = (float(base) - per_stop * customers - dwell * middle_yards + detour) / 60.0
        on_duty = (float(base) + detour + fill) / 60.0
        worst_drive, worst_duty = max(worst_drive, driving), max(worst_duty, on_duty)
        if driving > limit_drive or on_duty > limit_duty:
            which = "both" if driving > limit_drive and on_duty > limit_duty else ("driving" if driving > limit_drive else "on_duty")
            over.append({"route_id": route_id, "driving_hours": round(driving, 1), "on_duty_hours": round(on_duty, 1), "limit": which})
    message = ("All routes within federal hours-of-service limits" if not over
               else f"{len(over)} route{'s' if len(over) != 1 else ''} over federal hours-of-service limits")
    return {"limit_driving_hours": limit_drive, "limit_on_duty_hours": limit_duty,
            "max_driving_hours": round(worst_drive, 1), "max_on_duty_hours": round(worst_duty, 1),
            "routes_over": over, "message": message}


def local_routes(con: duckdb.DuckDBPyConnection, cfg: dict) -> list[str]:
    """Route IDs that start at the Tucson yard (the local delivery routes)."""
    yard = config.get(cfg, "yards.tucson_yard.id")
    return [r[0] for r in con.execute("SELECT route_id FROM dim_route WHERE yard = ? ORDER BY route_id", [yard]).fetchall()]


def compute_results(con: duckdb.DuckDBPyConnection, cfg: dict) -> dict:
    """Run every analysis against an open hub connection and return JSON-ready results."""
    params = SavingsParams.from_config(cfg)
    inputs: Inputs = load_inputs(con, cfg)
    tucson_routes = local_routes(con, cfg)
    confirmed = find_missed(inputs, params, include_unverified=False)
    incl = find_missed(inputs, params, include_unverified=True)
    missed = missed_block(confirmed, cfg)
    missed_incl = missed_block(incl, cfg)
    break_even = detour_options.break_even_table(inputs, params, cfg, tucson_routes)
    trucks = trucks_in_fleet(con)
    days = inputs.period_days()
    missed["usd_per_truck_per_year"] = per_truck_per_year(missed["total_usd"], days, trucks)
    missed_incl["usd_per_truck_per_year"] = per_truck_per_year(missed_incl["total_usd"], days, trucks)
    card_gal = float(con.execute("SELECT coalesce(sum(gallons), 0) FROM fact_fuel_txn").fetchone()[0])
    missed["card_gallons"] = missed_incl["card_gallons"] = int(round(card_gal))
    missed["cpg_per_card_gallon"] = cents_per_gallon(missed["total_usd"], card_gal)
    missed["cpg_per_card_gallon_incl_unverified"] = missed_incl["cpg_per_card_gallon"] = cents_per_gallon(missed_incl["total_usd"], card_gal)
    scout = partner_scout.compute_scout(inputs, params, cfg)
    for entry in scout:
        for point in entry["curve"]:
            point["annual_net_savings_per_truck_usd"] = round(point["annual_net_savings_usd"] / trucks, 2)
    return {
        "price_history": price_history_block(con, cfg),
        "station_stats": station_stats_block(con, break_even),
        "missed": missed,
        "missed_incl_unverified": missed_incl,
        "detours": detour_options.compute_detours(inputs, params, cfg, tucson_routes),
        "partner_scout": scout,
        "kpis": kpis(con, cfg, missed["total_usd"], trucks, days),
        "hos_check": hos_check(con, cfg),
        "notes": {
            "fills_analysed": len(inputs.fills), "skipped": inputs.skipped,
            "detour_rows": int(sum(len(v) for v in inputs.detours.values())),
            "trucks_in_fleet": trucks, "yard_has_tank": bool(tank_yards(cfg)),
        },
    }


def result_notes(results: dict) -> dict[str, str]:
    """A plain-English line of counts for each stored result (contract 6.1)."""
    info = results["notes"]
    skipped = info["skipped"]
    fills = (f"{info['fills_analysed']} matched fills on a known route day were analysed; {skipped.get('unmatched_station', 0)} had no matched station, "
             f"{skipped.get('no_route_that_day', 0)} had no route that day, and {skipped.get('actual_station_has_no_detour', 0)} were skipped "
             "because the station used has no detour entry to price it fairly.")
    return {
        "missed": f"Confirmed-truck-access alternatives only: {results['missed']['fill_count']} fills qualify. " + fills,
        "missed_incl_unverified": f"Unverified stations allowed as alternatives: {results['missed_incl_unverified']['fill_count']} fills qualify. " + fills,
        "partner_scout": f"{len(results["partner_scout"])} Tucson candidate stations scored. " + fills,
        "detours": f"Test route options for {len(results['detours']['routes'])} Tucson routes, from {info['detour_rows']} detour rows.",
        "price_history": "Weekly price paid, posted price and gallons for every station, with the EIA weekly series.",
        "station_stats": "Per-station fills, gallons, average prices and break-even gap over the whole period.",
        "hos_check": "Hours-of-service check: every route day with the longest allowed fuel stop added, against the federal driving and on-duty limits.",
        "kpis": "The Overview tiles; missed savings is the confirmed-only total. Average net price is card fills only; the partner share is Tucson yard trucks' card gallons. "
                + ("Yard gallons come from fact_yard_fuel." if info["yard_has_tank"]
                   else "No yard has an on-site tank, so yard gallons are 0."),
        "notes": "Counts behind the other results.",
    }


def save_results(con: duckdb.DuckDBPyConnection, results: dict) -> None:
    """Store each result as one JSON row in an_result (contract 6.1), replacing the older copy."""
    con.execute(f'DROP TABLE IF EXISTS "{RESULT_TABLE}"')
    con.execute(f'CREATE TABLE "{RESULT_TABLE}" (name VARCHAR PRIMARY KEY, payload VARCHAR NOT NULL, notes VARCHAR)')
    notes = result_notes(results)
    for name, payload in results.items():
        con.execute(f'INSERT INTO "{RESULT_TABLE}" VALUES (?, ?, ?)', [name, json.dumps(payload), notes.get(name)])


def read_results(con: duckdb.DuckDBPyConnection) -> dict:
    """Read the stored analytics results back. Raises a plain-English error if analytics has not run."""
    exists = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [RESULT_TABLE]).fetchone()[0]
    if not exists:
        raise RuntimeError("No analytics results in the hub yet. Run `uv run fleetfuel build` (or analytics.run) first.")
    return {name: json.loads(payload) for name, payload in con.execute(f'SELECT name, payload FROM "{RESULT_TABLE}"').fetchall()}


def run(cfg: dict, db_path: Path | str | None = None, **_: object) -> None:
    """Entry point called by the CLI (docs/contracts.md section 8)."""
    path = Path(db_path or config.DB_PATH)
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found. Run the ingest and normalize steps first (`uv run fleetfuel build`).")
    con = duckdb.connect(str(path))
    try:
        n_detour = con.execute("SELECT count(*) FROM ref_detour").fetchone()[0]
        if n_detour == 0:
            print("[analytics] warning: ref_detour is empty, so no alternative can be priced. Run the routing step first.")
        results = compute_results(con, cfg)
        save_results(con, results)
    finally:
        con.close()
    m, mi = results["missed"], results["missed_incl_unverified"]
    print(f"[analytics] {results['notes']['fills_analysed']} fills analysed; missed savings ${m['total_usd']:.2f} over {m['fill_count']} fills "
          f"(${mi['total_usd']:.2f} over {mi['fill_count']} incl. unverified)")
    print(f"[analytics] partnership scout: {len(results["partner_scout"])} Tucson candidate stations")

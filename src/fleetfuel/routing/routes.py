"""Where routing reads its routes from: `dim_route` in the hub database, else the route plan export.

In business terms: the stop list of each route is fixed for the whole period, so one stop list per
route is all the road calculations need. Also works out the time spent at stops (unloading at
customers, swapping trailers at the middle yard of a linehaul run), which is added to road minutes
to get `base_minutes`.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd

from fleetfuel import config

CUSTOMER_PREFIX = "CUST-"


def _from_db(db_path: Path) -> dict[str, dict]:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        has = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'dim_route'").fetchone()[0]
        rows = con.execute("SELECT route_id, yard, stops_json FROM dim_route ORDER BY route_id").fetchall() if has else []
    finally:
        con.close()
    return {r[0]: {"yard": r[1], "stops": json.loads(r[2])} for r in rows}


def _from_plans(raw_dir: Path) -> dict[str, dict]:
    path = raw_dir / "route_plans.csv"
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found. Run `uv run fleetfuel synth` first.")
    plans = pd.read_csv(path)
    out: dict[str, dict] = {}
    for route_id, plan in plans.groupby("RouteID"):
        first = plan[plan["PlanDate"] == plan["PlanDate"].min()].sort_values("StopSeq")
        stops = [
            {"seq": int(r.StopSeq), "location_id": r.LocationID, "lat": float(r.Lat), "lon": float(r.Lon)}
            for r in first.itertuples(index=False)
        ]
        out[str(route_id)] = {"yard": stops[0]["location_id"], "stops": stops}
    return out


def load_routes(db_path: Path | None = None, raw_dir: Path | None = None) -> dict[str, dict]:
    """{route_id: {"yard", "stops": [{"seq", "location_id", "lat", "lon", ...}]}} for every route.

    Reads `dim_route` when the hub database has it, otherwise the first plan day of route_plans.csv.
    """
    db_file = Path(db_path or config.DB_PATH)
    if db_file.is_file():
        found = _from_db(db_file)
        if found:
            return found
    return _from_plans(Path(raw_dir or config.RAW_DIR))


def stop_minutes(cfg: dict, stops: list[dict]) -> float:
    """Minutes spent standing still on a route: unloading at each customer, trailer swap at a middle yard."""
    customers = sum(1 for s in stops if str(s["location_id"]).startswith(CUSTOMER_PREFIX))
    middle_yards = sum(1 for s in stops[1:-1] if not str(s["location_id"]).startswith(CUSTOMER_PREFIX))
    return (
        customers * float(config.get(cfg, "routes.minutes_per_stop"))
        + middle_yards * float(config.get(cfg, "routes.linehaul_yard_dwell_minutes"))
    )

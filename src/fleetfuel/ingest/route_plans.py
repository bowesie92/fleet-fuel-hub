"""Route plan adapter: reads the routing-software export, one row per planned stop."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from fleetfuel.ingest.validate import LoadResult, blank, read_source_csv, split_rejects, to_number

SOURCE = "the route plan export (route_plans.csv)"
REQUIRED = [
    "PlanDate", "RouteID", "Vehicle", "StopSeq", "StopType", "LocationID", "Lat", "Lon", "PlannedArrival",
    "PlannedMilesFromPrev",
]
DATE_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%H:%M"


def load(path: Path) -> LoadResult:
    """Parse the route plan CSV. Planned times stay in the source timezone (HH:MM, with the plan date)."""
    raw = read_source_csv(path, REQUIRED, SOURCE)
    raw["plan_date"] = pd.to_datetime(raw["PlanDate"].str.strip(), format=DATE_FORMAT, errors="coerce")
    raw["planned_ts_source"] = pd.to_datetime(
        raw["PlanDate"].str.strip() + " " + raw["PlannedArrival"].str.strip(),
        format=f"{DATE_FORMAT} {TIME_FORMAT}",
        errors="coerce",
    )
    raw["stop_seq"] = to_number(raw["StopSeq"])
    raw["lat"] = to_number(raw["Lat"])
    raw["lon"] = to_number(raw["Lon"])
    raw["planned_miles_from_prev"] = to_number(raw["PlannedMilesFromPrev"])

    checks = [
        ("bad plan date", raw["plan_date"].isna()),
        ("blank route or vehicle", blank(raw["RouteID"]) | blank(raw["Vehicle"])),
        ("bad stop sequence", raw["stop_seq"].isna()),
        ("bad planned time", raw["planned_ts_source"].isna()),
        ("bad coordinates", raw["lat"].isna() | raw["lon"].isna()),
        ("bad planned miles", raw["planned_miles_from_prev"].isna()),
    ]
    good, _rejects, reasons = split_rejects(raw, checks)

    out = pd.DataFrame(
        {
            "plan_date": good["plan_date"],
            "route_id": good["RouteID"].str.strip(),
            "truck_id": good["Vehicle"].str.strip(),
            "stop_seq": good["stop_seq"].astype("int64"),
            "stop_type": good["StopType"].str.strip(),
            "location_id": good["LocationID"].str.strip(),
            "lat": good["lat"],
            "lon": good["lon"],
            "planned_ts_source": good["planned_ts_source"],
            "planned_miles_from_prev": good["planned_miles_from_prev"],
        }
    )
    out = out.sort_values(["plan_date", "route_id", "stop_seq"], kind="stable").reset_index(drop=True)
    return LoadResult(SOURCE, out, rows_in=len(raw), rejected=len(raw) - len(out), reasons=reasons)

"""Ingest: read each raw source file, validate its columns and types, and stage it in the hub.

Step 1 of `uv run fleetfuel build` (docs/contracts.md section 8). It starts a fresh
`hub.duckdb`, loads the clean reference tables (trucks, EIA prices, posted prices) and
stages the messy feeds (fuel card, telematics, route plans) and the station list in
temporary `stg_*` tables for `normalize.run`. Every source gets one `dq_log` row saying
how many rows came in, how many were usable and how many had problems.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from fleetfuel import config, db
from fleetfuel.ingest import fuel_card, reference, route_plans, telematics, yard_fuel
from fleetfuel.ingest.validate import LoadResult, SourceError

RAW_FILES = {
    "fleet": "fleet.csv",
    "partners": "partner_program.csv",
    "prices": "station_prices.csv",
    "eia": "eia_diesel.csv",
    "stations": "stations.geojson",
    "fuel_card": "fuel_card.csv",
    "telematics": "telematics.csv",
    "route_plans": "route_plans.csv",
    "yard_fuel": "yard_fuel.csv",
}

__all__ = ["run", "SourceError"]


def _log(con, check_name: str, result: LoadResult, extra_issue: int = 0, extra_note: str = "") -> None:
    """Write one dq_log row for a loaded source."""
    notes = result.note()
    if extra_note:
        notes = notes[:-1] + f"; {extra_note}."
    db.log_dq(con, check_name, result.rows_in, result.rows_out, result.issue_count + extra_issue, notes)


APPLIED_STATUSES = {"applied", "applied_new", "applied_matched"}


def _override_note(record: dict, stations: pd.DataFrame) -> str:
    """Plain-English sentence for one station override record (`stations` is the final station frame)."""
    action, status = record["action"], record["status"]
    state = "not applied (station not found)" if status == "not_found" else status
    if action == "add":
        landed = record["target_station_id"] or record["station_id"]
        match = stations[stations["station_id"] == landed]
        name = f" ({match.iloc[0]['name']})" if len(match) and match.iloc[0]["name"] else ""
        url = match.iloc[0]["source_url"] if len(match) else None
        verb = f"Added {landed}{name} from a chain locator" if status == "applied_new" else f"Confirmed {landed}{name} from a chain locator"
        return f"{verb}: {record['reason']} Source: {url}. Status: {state}."
    ids = record["station_id"] or "an unknown station"
    verb = {"exclude": f"Excluded {ids}", "rename": f"Renamed {ids}",
            "merge_into": f"Merged {ids} into {record['target_station_id'] or 'its target'}"}[action]
    return f"{verb} ({record['osm_ref']}): {record['reason']} Status: {state}."


def run(cfg: dict, *, raw_dir: Path | None = None, db_path: Path | None = None, overrides_path: Path | None = None) -> None:
    """Load and validate every raw file into a fresh hub database.

    Raises a plain-English `SourceError` if a file is missing or lacks a required column.
    """
    raw = Path(raw_dir) if raw_dir is not None else config.RAW_DIR
    con = db.connect(db_path if db_path is not None else config.DB_PATH)
    try:
        db.create_schema(con)
        paths = {key: raw / name for key, name in RAW_FILES.items()}

        fleet = reference.load_fleet(paths["fleet"])
        db.insert_frame(con, "dim_truck", fleet.df[["truck_id", "home_yard", "role", "tank_gal", "mpg_assumed"]])
        db.stage_frame(con, "stg_fleet", fleet.df)
        _log(con, "ingest_fleet", fleet)
        print(f"[ingest] fleet: {fleet.rows_out} trucks")

        stations, override_records = reference.load_stations(raw, overrides_path)
        db.stage_frame(con, "stg_stations", stations.df)
        _log(con, "ingest_stations", stations)
        for record in override_records:
            applied = record["status"] in APPLIED_STATUSES
            db.log_dq(con, "station_override", 1, 1 if applied else 0, 0 if applied else 1, _override_note(record, stations.df))
        print(f"[ingest] stations: {stations.rows_out} stations")

        known_stations = set(stations.df["station_id"])
        partners = reference.load_partners(paths["partners"])
        unknown_partner = ~partners.df["station_id"].isin(known_stations)
        partners.df = partners.df[~unknown_partner].reset_index(drop=True)
        db.stage_frame(con, "stg_partner", partners.df)
        _log(
            con, "ingest_partner_program", partners, int(unknown_partner.sum()),
            f"{int(unknown_partner.sum())} partner rows name a station that is not on the station list" if unknown_partner.any() else "",
        )

        prices = reference.load_station_prices(paths["prices"])
        unknown_price = ~prices.df["station_id"].isin(known_stations)
        prices.df = prices.df[~unknown_price].reset_index(drop=True)
        db.insert_frame(con, "ref_station_price", prices.df)
        _log(
            con, "ingest_station_prices", prices, int(unknown_price.sum()),
            f"{int(unknown_price.sum())} price rows name a station that is not on the station list" if unknown_price.any() else "",
        )
        print(f"[ingest] posted prices: {prices.rows_out} rows")

        eia = reference.load_eia(paths["eia"])
        db.insert_frame(con, "ref_eia_weekly", eia.df)
        _log(con, "ingest_eia", eia)

        device_to_truck = dict(zip(fleet.df["telematics_device_id"], fleet.df["truck_id"]))
        card_to_truck = dict(zip(fleet.df["fuel_card_last4"], fleet.df["truck_id"]))
        card = fuel_card.load(paths["fuel_card"], card_to_truck=card_to_truck, known_trucks=set(fleet.df["truck_id"]))
        db.stage_frame(con, "stg_fuel_card", card.df)
        _log(con, "ingest_fuel_card", card)
        print(f"[ingest] fuel card: {card.rows_out} of {card.rows_in} rows usable")

        pings = telematics.load(paths["telematics"], device_to_truck=device_to_truck)
        db.stage_frame(con, "stg_telematics", pings.df)
        _log(con, "ingest_telematics", pings)
        print(f"[ingest] telematics: {pings.rows_out} of {pings.rows_in} pings usable")

        plans = route_plans.load(paths["route_plans"])
        db.stage_frame(con, "stg_route_plans", plans.df)
        _log(con, "ingest_route_plans", plans)
        print(f"[ingest] route plans: {plans.rows_out} stops")

        # On-site yard fills go straight to fact_yard_fuel and never enter the card pipeline.
        if paths["yard_fuel"].is_file():
            yard = yard_fuel.load(paths["yard_fuel"])
            db.insert_frame(con, "fact_yard_fuel", yard.df)
            db.log_dq(con, "ingest_yard_fuel", yard.rows_in, yard.rows_out, yard.issue_count, yard_fuel.note(yard))
            print(f"[ingest] yard fuel: {yard.rows_out} on-site fills")
        else:
            tank_yards = [
                config.get(cfg, f"yards.{name}.label")
                for name in config.get(cfg, "yards")
                if config.get(cfg, f"yards.{name}.onsite_fuel")
            ]
            if tank_yards:
                raise SourceError(
                    f"The on-site tank log (yard_fuel.csv) is missing at {paths['yard_fuel']}, but the config says "
                    f"{', '.join(tank_yards)} has an on-site fuel tank. Generate the synthetic data again or turn off onsite_fuel for that yard."
                )
            db.log_dq(con, "ingest_yard_fuel", 0, 0, 0, yard_fuel.missing_note())
            print("[ingest] yard fuel: no tank log, zero on-site fills")
    finally:
        con.close()

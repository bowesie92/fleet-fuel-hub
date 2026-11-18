"""Normalize: clean the staged data and build the hub tables.

Step 2 of `uv run fleetfuel build` (docs/contracts.md section 8). In order: put every
timestamp on Arizona local time, remove duplicate swipes, match swipes to stations (never
guessing), repair odometers from GPS, build the dimension and fact tables, and, when a
`ground_truth/` folder exists, score the cleanup. Each check writes one `dq_log` row.
Run `fleetfuel.ingest.run` first: it creates the database and the staging tables.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from fleetfuel import config, db
from fleetfuel.normalize import build, dedupe, evaluate, odometer, station_match, timezones
from fleetfuel.normalize.settings import setting

__all__ = ["run"]


def _convert_times(con, cfg: dict, card: pd.DataFrame, pings: pd.DataFrame, plans: pd.DataFrame) -> None:
    """Convert the three timestamp sources to local time and log what changed."""
    target = config.get(cfg, "period.timezone")
    source_tz = {s: config.get(cfg, f"pipeline.source_timezones.{s}") for s in ("fuel_card", "telematics", "route_plans")}

    card["ts_local"] = timezones.to_local(card["ts_source"], source_tz["fuel_card"], target)
    shifted = timezones.count_shifted(card["ts_source"], card["ts_local"])
    db.log_dq(
        con, "timezone_fuel_card", len(card), len(card), shifted,
        f"Fuel card times are recorded in {source_tz['fuel_card']}; {shifted} needed shifting to {target} time.",
    )

    pings["ts_local"] = timezones.to_local(pings["ts_source"], source_tz["telematics"], target)
    shifted = timezones.count_shifted(pings["ts_source"], pings["ts_local"])
    db.log_dq(
        con, "timezone_telematics", len(pings), len(pings), shifted,
        f"GPS pings are exported in {source_tz['telematics']}; {shifted} pings were converted to {target} time.",
    )

    plans["planned_ts_local"] = timezones.to_local(plans["planned_ts_source"], source_tz["route_plans"], target)

    tolerance = setting(cfg, "pipeline.timezones.start_diff_tolerance_minutes")
    sanity = timezones.start_time_sanity(pings, plans, tolerance)
    db.log_dq(con, "timezone_start_sanity", sanity.compared, sanity.compared, sanity.flagged, sanity.note(tolerance))


def _log_match(con, kept: pd.DataFrame, result: station_match.MatchResult) -> None:
    """Log the station matching outcome."""
    matched = result.matches["station_id"].notna()
    unmatched_n = int((~matched).sum())
    mean_conf = float(result.matches.loc[matched, "confidence"].mean()) if matched.any() else float("nan")
    note = f"Matched {int(matched.sum())} of {len(kept)} swipes to a station"
    if matched.any():
        note += f" (average confidence {mean_conf:.2f})"
    note += f"; {unmatched_n} were left unmatched rather than guessed."
    db.log_dq(con, "station_match", len(kept), int(matched.sum()), unmatched_n, note)


def run(cfg: dict, *, raw_dir: Path | None = None, db_path: Path | None = None) -> None:
    """Clean the staged data into the contract tables and score it against ground truth if available."""
    raw = Path(raw_dir) if raw_dir is not None else config.RAW_DIR
    con = db.connect(db_path if db_path is not None else config.DB_PATH)
    try:
        card, pings, plans, stations, partners = (
            db.read_table(con, t) for t in ("stg_fuel_card", "stg_telematics", "stg_route_plans", "stg_stations", "stg_partner")
        )
        _convert_times(con, cfg, card, pings, plans)
        print(f"[normalize] timezones converted: {len(pings)} pings, {len(card)} swipes")

        window = setting(cfg, "pipeline.dedupe.window_minutes")
        tolerance = setting(cfg, "pipeline.dedupe.amount_tolerance_usd")
        kept, removed = dedupe.remove_duplicates(card, window, tolerance)
        db.insert_frame(con, "dq_duplicate", removed)
        db.log_dq(
            con, "dedupe_swipes", len(card), len(kept), len(removed),
            f"Removed {len(removed)} duplicate card swipes (same truck, within {window:g} minutes, same amount); the first swipe was kept.",
        )
        print(f"[normalize] duplicates removed: {len(removed)}")

        result = station_match.match_stations(kept, stations, pings, cfg)
        db.insert_frame(con, "dq_station_unmatched", result.unmatched)
        _log_match(con, kept, result)
        print(f"[normalize] stations matched: {int(result.matches['station_id'].notna().sum())} of {len(kept)}")

        max_gap = setting(cfg, "pipeline.odometer.max_gap_vs_telematics_mi")
        repaired, stats = odometer.repair_odometers(kept, pings, max_gap)
        db.log_dq(
            con, "odometer_repair", len(repaired), len(repaired), stats.repaired + stats.unrepairable,
            f"Repaired {stats.repaired} odometers from GPS ({stats.blank} blank, {stats.wrong} implausible); "
            f"{stats.unrepairable} could not be repaired because GPS did not cover the swipe.",
        )
        print(f"[normalize] odometers repaired: {stats.repaired}")

        facts = repaired.merge(result.matches, on="source_row", how="left")
        db.insert_frame(
            con,
            "fact_fuel_txn",
            pd.DataFrame(
                {
                    "txn_id": facts["txn_id"], "truck_id": facts["truck_id"], "station_id": facts["station_id"],
                    "ts_local": facts["ts_local"], "gallons": facts["gallons"], "price_per_gal": facts["price_per_gal"],
                    "total": facts["total"], "odometer": facts["odometer"],
                    "odometer_repaired_flag": facts["odometer_repaired_flag"], "source_row": facts["source_row"],
                    "station_match_confidence": facts["confidence"], "station_match_method": facts["method"],
                }
            ),
        )
        db.insert_frame(con, "dim_station", build.build_dim_station(stations, partners, result.aliases))
        db.insert_frame(con, "fact_ping", pings[["truck_id", "ts_local", "lat", "lon", "odometer"]])

        routes, changing = build.build_dim_route(plans, cfg)
        db.insert_frame(con, "dim_route", routes)
        db.log_dq(
            con, "route_plan_stability", len(routes), len(routes), changing,
            f"{changing} of {len(routes)} routes change their stop list during the period (the contract says none should).",
        )
        route_days = build.build_fact_route_day(plans, pings)
        db.insert_frame(con, "fact_route_day", route_days)
        missing_miles = int(route_days["actual_miles"].isna().sum())
        db.log_dq(
            con, "route_day_miles", len(route_days), len(route_days) - missing_miles, missing_miles,
            f"Worked out miles driven for {len(route_days) - missing_miles} of {len(route_days)} route days from the GPS odometer.",
        )

        scored = evaluate.evaluate(con, raw / "ground_truth", setting(cfg, "pipeline.odometer.eval_tolerance_mi"))
        print(f"[normalize] accuracy metrics scored: {scored}" if scored else "[normalize] no ground truth found, accuracy not scored")
        db.drop_staging(con)
    finally:
        con.close()

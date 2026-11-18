"""Reference data adapters: fleet, partner program, station prices, stations and EIA weekly diesel.

These are the clean master files (contract 4.1 to 4.6). They still get the same checks as
the messy feeds: required columns, parsed types and a count of any rows set aside.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from fleetfuel import stations as stations_module
from fleetfuel.ingest.validate import LoadResult, SourceError, blank, read_source_csv, split_rejects, to_number

FLEET_REQUIRED = ["truck_id", "home_yard", "role", "tank_gal", "mpg_assumed", "telematics_device_id", "fuel_card_last4"]
PARTNER_REQUIRED = ["station_id", "partner_discount_cpg"]
PRICE_REQUIRED = ["date", "station_id", "posted_price_per_gal"]
EIA_REQUIRED = ["week_start", "price_per_gal", "series_id"]
DATE_FORMAT = "%Y-%m-%d"


def load_fleet(path: Path) -> LoadResult:
    """Read fleet.csv: one row per truck, with the device and card crosswalk."""
    raw = read_source_csv(path, FLEET_REQUIRED, "the fleet list (fleet.csv)")
    raw["tank_gal"] = to_number(raw["tank_gal"])
    raw["mpg_assumed"] = to_number(raw["mpg_assumed"])
    raw["truck_id"] = raw["truck_id"].str.strip()
    checks = [
        ("blank truck id", blank(raw["truck_id"])),
        ("repeated truck id", raw["truck_id"].duplicated(keep="first")),
        ("bad tank size or mpg", raw["tank_gal"].isna() | raw["mpg_assumed"].isna()),
        ("blank device id", blank(raw["telematics_device_id"])),
    ]
    good, _rejects, reasons = split_rejects(raw, checks)
    out = pd.DataFrame(
        {
            "truck_id": good["truck_id"],
            "home_yard": good["home_yard"].str.strip(),
            "role": good["role"].str.strip(),
            "tank_gal": good["tank_gal"],
            "mpg_assumed": good["mpg_assumed"],
            "telematics_device_id": good["telematics_device_id"].str.strip(),
            "fuel_card_last4": good["fuel_card_last4"].str.strip(),
        }
    ).reset_index(drop=True)
    return LoadResult("the fleet list (fleet.csv)", out, rows_in=len(raw), rejected=len(raw) - len(out), reasons=reasons)


def load_partners(path: Path) -> LoadResult:
    """Read partner_program.csv: the stations that give a discount, in cents per gallon."""
    raw = read_source_csv(path, PARTNER_REQUIRED, "the partner list (partner_program.csv)")
    raw["partner_discount_cpg"] = to_number(raw["partner_discount_cpg"])
    raw["station_id"] = raw["station_id"].str.strip()
    checks = [
        ("blank station id", blank(raw["station_id"])),
        ("repeated station id", raw["station_id"].duplicated(keep="first")),
        ("bad discount", raw["partner_discount_cpg"].isna() | (raw["partner_discount_cpg"] < 0)),
    ]
    good, _rejects, reasons = split_rejects(raw, checks)
    out = good[["station_id", "partner_discount_cpg"]].reset_index(drop=True)
    return LoadResult("the partner list (partner_program.csv)", out, rows_in=len(raw), rejected=len(raw) - len(out), reasons=reasons)


def load_station_prices(path: Path) -> LoadResult:
    """Read station_prices.csv: the posted pump price per station per day."""
    raw = read_source_csv(path, PRICE_REQUIRED, "the posted price feed (station_prices.csv)")
    raw["date"] = pd.to_datetime(raw["date"].str.strip(), format=DATE_FORMAT, errors="coerce")
    raw["posted_price_per_gal"] = to_number(raw["posted_price_per_gal"])
    raw["station_id"] = raw["station_id"].str.strip()
    checks = [
        ("bad date", raw["date"].isna()),
        ("blank station id", blank(raw["station_id"])),
        ("bad price", raw["posted_price_per_gal"].isna() | (raw["posted_price_per_gal"] <= 0)),
    ]
    good, _rejects, reasons = split_rejects(raw, checks)
    out = good[["station_id", "date", "posted_price_per_gal"]].reset_index(drop=True)
    out["date"] = out["date"].dt.normalize()
    return LoadResult(
        "the posted price feed (station_prices.csv)", out, rows_in=len(raw), rejected=len(raw) - len(out), reasons=reasons
    )


def load_eia(path: Path) -> LoadResult:
    """Read eia_diesel.csv: weekly regional diesel price. A repeated week keeps its first row."""
    raw = read_source_csv(path, EIA_REQUIRED, "the EIA weekly diesel file (eia_diesel.csv)")
    raw["week_start"] = pd.to_datetime(raw["week_start"].str.strip(), format=DATE_FORMAT, errors="coerce")
    raw["price_per_gal"] = to_number(raw["price_per_gal"])
    checks = [
        ("bad week", raw["week_start"].isna()),
        ("bad price", raw["price_per_gal"].isna() | (raw["price_per_gal"] <= 0)),
        ("repeated week", raw["week_start"].duplicated(keep="first")),
    ]
    good, _rejects, reasons = split_rejects(raw, checks)
    out = good[["week_start", "price_per_gal"]].sort_values("week_start").reset_index(drop=True)
    return LoadResult(
        "the EIA weekly diesel file (eia_diesel.csv)", out, rows_in=len(raw), rejected=len(raw) - len(out), reasons=reasons
    )


def load_stations(raw_dir: Path, overrides_path: Path | None = None) -> tuple[LoadResult, list[dict]]:
    """Read the station list through `fleetfuel.stations.load_stations` (contract 4.11), overrides applied.

    Returns the stations plus one record per override entry. `merged_osm_refs` is stored as JSON text
    so the frame can be staged in DuckDB.
    """
    source = "the station list (stations.geojson)"
    path = Path(raw_dir) / "stations.geojson"
    if not path.is_file():
        raise SourceError(f"{source} not found at {path}. Run `uv run fleetfuel fetch` first.")
    try:
        frame, records = stations_module.load_stations(raw_dir=raw_dir, overrides_path=overrides_path)
    except KeyError as err:
        raise SourceError(f"{source} is missing required properties: {err}") from err
    removed = sum(1 for r in records if r["status"] == "applied" and r["action"] in ("exclude", "merge_into"))
    created = sum(1 for r in records if r["status"] == "applied_new")
    rows_in = len(frame) + removed - created  # rows in stations.geojson
    frame = frame.assign(merged_osm_refs=frame["merged_osm_refs"].map(json.dumps))
    dropped = removed
    result = LoadResult(source, frame, rows_in=rows_in, rejected=0)
    if dropped:
        result.extra_issues = dropped
        result.extra_notes.append(f"{dropped} stations were dropped or merged by manual overrides")
    return result, records

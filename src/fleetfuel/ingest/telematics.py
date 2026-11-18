"""Telematics adapter: reads the GPS/ELD ping export and returns typed, validated pings.

Event times stay exactly as exported (UTC, no offset marker); normalize/timezones.py
converts them to Arizona local time. Device IDs are mapped to trucks through fleet.csv.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from fleetfuel.ingest.validate import LoadResult, blank, read_source_csv, split_rejects, to_number

SOURCE = "the telematics export (telematics.csv)"
REQUIRED = ["device_id", "event_time", "lat", "lon", "odometer_mi", "speed_mph"]
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
MAX_ABS_LAT = 90.0  # a latitude outside this is not a real coordinate
MAX_ABS_LON = 180.0  # a longitude outside this is not a real coordinate


def load(path: Path, device_to_truck: dict[str, str] | None = None) -> LoadResult:
    """Parse the telematics CSV. Pings from devices not in fleet.csv are set aside and counted."""
    raw = read_source_csv(path, REQUIRED, SOURCE)
    raw["device_id"] = raw["device_id"].str.strip()
    raw["ts_source"] = pd.to_datetime(raw["event_time"].str.strip(), format=TIME_FORMAT, errors="coerce")
    raw["lat"] = to_number(raw["lat"])
    raw["lon"] = to_number(raw["lon"])
    raw["odometer"] = to_number(raw["odometer_mi"])
    raw["speed_mph"] = to_number(raw["speed_mph"])
    raw["truck_id"] = raw["device_id"].map(device_to_truck) if device_to_truck is not None else raw["device_id"]

    checks = [
        ("unknown device", raw["truck_id"].isna() | blank(raw["device_id"])),
        ("bad time", raw["ts_source"].isna()),
        (
            "bad coordinates",
            raw["lat"].isna() | raw["lon"].isna() | (raw["lat"].abs() > MAX_ABS_LAT) | (raw["lon"].abs() > MAX_ABS_LON),
        ),
    ]
    good, _rejects, reasons = split_rejects(raw, checks)

    out = good[["device_id", "truck_id", "ts_source", "lat", "lon", "odometer", "speed_mph"]].copy()
    parsed = len(out)
    out = out.drop_duplicates(subset=["device_id", "ts_source"], keep="first")
    repeated = parsed - len(out)
    out = out.sort_values(["truck_id", "ts_source"], kind="stable").reset_index(drop=True)

    result = LoadResult(SOURCE, out, rows_in=len(raw), rejected=len(raw) - parsed, reasons=reasons)
    if repeated:
        result.extra_issues = repeated
        result.extra_notes.append(f"{repeated} repeated pings (same device and time) were collapsed to one")
    return result

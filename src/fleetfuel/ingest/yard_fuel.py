"""Yard fuel adapter: reads the Phoenix DC tank log (on-site fills, not fuel card transactions)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from fleetfuel.ingest.validate import LoadResult, blank, read_source_csv, split_rejects, to_number

SOURCE = "the Phoenix DC tank log (yard_fuel.csv)"
REQUIRED = ["FillDate", "FillTime", "Yard", "Unit", "Gallons", "PricePerGal", "Total"]
DATE_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%H:%M"


def load(path: Path) -> LoadResult:
    """Parse the tank log. Times are already local Arizona time, so no timezone shift is needed."""
    raw = read_source_csv(path, REQUIRED, SOURCE)
    raw["ts_local"] = pd.to_datetime(
        raw["FillDate"].str.strip() + " " + raw["FillTime"].str.strip(), format=f"{DATE_FORMAT} {TIME_FORMAT}", errors="coerce"
    )
    raw["gallons"] = to_number(raw["Gallons"])
    raw["price_per_gal"] = to_number(raw["PricePerGal"])
    raw["total"] = to_number(raw["Total"])
    checks = [
        ("bad date or time", raw["ts_local"].isna()),
        ("blank truck or yard", blank(raw["Unit"]) | blank(raw["Yard"])),
        ("bad gallons", raw["gallons"].isna() | (raw["gallons"] <= 0)),
        ("bad price", raw["price_per_gal"].isna() | (raw["price_per_gal"] <= 0)),
        ("bad total", raw["total"].isna()),
    ]
    good, _rejects, reasons = split_rejects(raw, checks)
    out = pd.DataFrame(
        {
            "truck_id": good["Unit"].str.strip(), "yard": good["Yard"].str.strip(), "ts_local": good["ts_local"],
            "gallons": good["gallons"], "price_per_gal": good["price_per_gal"], "total": good["total"],
        }
    )
    out = out.sort_values(["ts_local", "truck_id"], kind="stable").reset_index(drop=True)
    return LoadResult(SOURCE, out, rows_in=len(raw), rejected=len(raw) - len(out), reasons=reasons)


def missing_note() -> str:
    """Plain-English dq_log note for a data set that has no tank log file."""
    return (
        "No on-site tank log (yard_fuel.csv) was found, so zero on-site fills were loaded. "
        "That is valid for a yard with no fuel tank; it is not an error."
    )


def note(result: LoadResult) -> str:
    """Plain-English dq_log note for a loaded tank log."""
    text = f"Read {result.rows_out} on-site fills from the Phoenix DC tank log (yard_fuel.csv); these are not fuel card transactions"
    if result.rejected:
        detail = ", ".join(f"{count} {reason}" for reason, count in result.reasons.most_common())
        text += f"; {result.rejected} rows could not be used and were set aside ({detail})"
    return text + "."

"""Fuel card adapter: reads the vendor-style export and returns typed, validated swipes.

The vendor columns (TransDate, MerchantName...) stay messy on purpose in names and
odometers, but dates, gallons and prices must parse. Rows that do not parse are counted.
The timestamp stays in the source timezone here; normalize/timezones.py converts it.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from fleetfuel.ingest.validate import LoadResult, blank, read_source_csv, split_rejects, to_number

SOURCE = "the fuel card export (fuel_card.csv)"
REQUIRED = [
    "TransactionID", "CardLast4", "Unit", "TransDate", "TransTime", "MerchantName", "MerchantCity",
    "MerchantState", "Product", "Gallons", "PPG", "Total", "Odometer",
]
DATE_TIME_FORMAT = "%m/%d/%Y %H:%M"


def load(path: Path, card_to_truck: dict[str, str] | None = None, known_trucks: set[str] | None = None) -> LoadResult:
    """Parse the fuel card CSV. `known_trucks` and `card_to_truck` (from fleet.csv) validate the truck.

    A blank or unknown `Unit` is recovered from the card's last four digits when possible.
    """
    raw = read_source_csv(path, REQUIRED, SOURCE)
    raw["source_row"] = range(1, len(raw) + 1)

    unit = raw["Unit"].str.strip()
    if known_trucks is not None:
        recovered = raw["CardLast4"].str.strip().map(card_to_truck or {})
        unknown = ~unit.isin(known_trucks)
        unit = unit.where(~unknown, recovered.fillna(""))
    raw["truck_id"] = unit

    txn_id = raw["TransactionID"].str.strip()
    raw["ts_source"] = pd.to_datetime(
        raw["TransDate"].str.strip() + " " + raw["TransTime"].str.strip(), format=DATE_TIME_FORMAT, errors="coerce"
    )
    raw["gallons"] = to_number(raw["Gallons"])
    raw["price_per_gal"] = to_number(raw["PPG"])
    raw["total"] = to_number(raw["Total"])
    raw["odometer_raw"] = to_number(raw["Odometer"])

    checks = [
        ("blank transaction id", blank(raw["TransactionID"])),
        ("repeated transaction id", txn_id.duplicated(keep="first")),
        ("unknown truck", blank(raw["truck_id"])),
        ("bad date or time", raw["ts_source"].isna()),
        ("bad gallons", raw["gallons"].isna() | (raw["gallons"] <= 0)),
        ("bad price", raw["price_per_gal"].isna() | (raw["price_per_gal"] <= 0)),
        ("bad total", raw["total"].isna() | (raw["total"] < 0)),
        ("blank merchant name", blank(raw["MerchantName"])),
    ]
    good, _rejects, reasons = split_rejects(raw, checks)

    odometer_unusable = int(good["odometer_raw"].isna().sum())
    city = good["MerchantCity"].str.strip()
    out = pd.DataFrame(
        {
            "source_row": good["source_row"].astype("int64"),
            "txn_id": good["TransactionID"].str.strip(),
            "card_last4": good["CardLast4"].str.strip(),
            "truck_id": good["truck_id"],
            "ts_source": good["ts_source"],
            "merchant_name": good["MerchantName"].str.strip(),
            "merchant_city": city.where(city != "", None),
            "gallons": good["gallons"],
            "price_per_gal": good["price_per_gal"],
            "total": good["total"],
            "odometer_raw": good["odometer_raw"],
        }
    ).reset_index(drop=True)

    result = LoadResult(SOURCE, out, rows_in=len(raw), rejected=len(raw) - len(out), reasons=reasons)
    result.extra_issues = odometer_unusable
    if odometer_unusable:
        result.extra_notes.append(f"{odometer_unusable} swipes have a blank or unreadable odometer, left for the repair step")
    return result

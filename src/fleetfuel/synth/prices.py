"""synth/prices.py: the posted pump price at every station on every day, and the partner program.

Business terms: price = the EIA weekly regional price for that week + a station-class
offset + a fixed per-station offset + small daily noise. Truck-confirmed stations inside the partner box
are fuel partners and give a cents-per-gallon discount off the posted price.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .params import Params

PRICE_DECIMALS = 3  # Contract 4.6: posted prices have 3 decimals
DAYS_PER_EIA_WEEK = 7  # Fact: each EIA row covers a 7-day week starting at week_start


@dataclass
class PriceBook:
    """Posted prices (days x stations), partner discounts, and notes on EIA coverage."""

    days: list[date]
    posted: np.ndarray  # shape (n_days, n_stations), dollars per gallon
    discount_cpg: np.ndarray  # shape (n_stations,), cents per gallon off for partners, else 0
    carry_forward_days: int  # days after the last EIA week that reuse its price
    backfill_days: int  # days before the first EIA week that reuse its price
    eia: np.ndarray  # shape (n_days,), the EIA weekly price in force each day, dollars per gallon


def calendar_days(start: date, end: date) -> list[date]:
    """Every calendar day from start to end inclusive."""
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def eia_price_for_days(eia: pd.DataFrame, days: list[date]) -> tuple[np.ndarray, int, int]:
    """Look up the EIA weekly price for each day, carrying the last known week forward if needed.

    Returns (prices, days_carried_forward_past_last_week, days_before_first_week).
    """
    weeks = np.array([np.datetime64(w) for w in eia["week_start"]], dtype="datetime64[D]")
    prices = eia["price_per_gal"].to_numpy(dtype=float)
    day_arr = np.array([np.datetime64(d) for d in days], dtype="datetime64[D]")
    idx = np.searchsorted(weeks, day_arr, side="right") - 1
    before = int((idx < 0).sum())
    idx = np.clip(idx, 0, len(weeks) - 1)
    last_covered = weeks[-1] + np.timedelta64(DAYS_PER_EIA_WEEK - 1, "D")
    carried = int((day_arr > last_covered).sum())
    return prices[idx], carried, before


def partner_mask(stations: pd.DataFrame, bbox: tuple[float, float, float, float],
                 named_only: bool) -> np.ndarray:
    """True for every truck-confirmed station inside the partner box (south, west, north, east).

    Business terms: the partner program is a stated assumption, not a draw. An empty box is an
    error, because a program with no stations would silently price nothing at a discount.
    """
    south, west, north, east = bbox
    inside = stations["lat"].between(south, north) & stations["lon"].between(west, east)
    keep = inside & (stations["truck_access"] == "confirmed")
    if named_only:  # unnamed truck-tagged points are not real partner sites
        keep &= stations["name"].notna() | stations["brand"].notna()
    mask = keep.to_numpy()
    if not mask.any():
        rule = " with an OSM name or brand (prices.partner_set_named_only is on)" if named_only else ""
        raise ValueError(
            f"No truck-confirmed station{rule} lies inside prices.partner_set_bbox {list(bbox)}; "
            "widen the box or check the station list."
        )
    return mask


def build_price_book(p: Params, stations: pd.DataFrame, eia: pd.DataFrame, rng: np.random.Generator) -> PriceBook:
    """Create the daily posted price grid and apply the partner discount."""
    days = calendar_days(p.start, p.end)
    base, carried, before = eia_price_for_days(eia, days)
    n = len(stations)
    class_offset = stations["station_class"].map(p.class_offsets).to_numpy(dtype=float)
    station_offset = rng.normal(0.0, p.station_offset_sd, size=n)
    noise = rng.normal(0.0, p.daily_noise_sd, size=(len(days), n))
    posted = np.round(base[:, None] + class_offset[None, :] + station_offset[None, :] + noise, PRICE_DECIMALS)
    discount = np.where(partner_mask(stations, p.partner_bbox, p.partner_named_only), p.partner_discount_cpg, 0.0)
    return PriceBook(days, posted, discount, carried, before, base)


def partner_table(stations: pd.DataFrame, book: PriceBook) -> pd.DataFrame:
    """The partner_program.csv rows: partner station IDs and their discount, sorted by station_id."""
    mask = book.discount_cpg > 0
    out = pd.DataFrame(
        {"station_id": stations["station_id"][mask].to_numpy(), "partner_discount_cpg": book.discount_cpg[mask]}
    )
    return out.sort_values("station_id").reset_index(drop=True)


def price_table(stations: pd.DataFrame, book: PriceBook) -> pd.DataFrame:
    """The station_prices.csv rows: one per station per calendar day, sorted by date then station_id."""
    n_days, n = book.posted.shape
    out = pd.DataFrame(
        {
            "date": np.repeat(np.array(book.days, dtype=object), n),
            "station_id": np.tile(stations["station_id"].to_numpy(), n_days),
            "posted_price_per_gal": book.posted.reshape(-1),
        }
    )
    return out.sort_values(["date", "station_id"], kind="stable").reset_index(drop=True)

"""Odometer repair: fix blank or implausible card odometers using the GPS odometer.

Drivers type the odometer at the pump, so it is sometimes blank or mistyped. The telematics
odometer is reliable and only ever increases, so the truck's reading at the swipe time can
be interpolated between the two surrounding pings. A card value is trusted unless it is
blank, not positive, or further than `pipeline.odometer.max_gap_vs_telematics_mi` from GPS.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

ODOMETER_DECIMALS = 1  # telematics reports tenths of a mile, so repaired values do too
EPOCH = pd.Timestamp("1970-01-01")
SECONDS = pd.Timedelta(seconds=1)


@dataclass
class OdometerStats:
    """How many swipes needed work, for the dq_log row."""

    blank: int
    wrong: int
    repaired: int
    unrepairable: int


def _seconds(ts: pd.Series) -> np.ndarray:
    """Timestamps as float seconds since 1970, for interpolation."""
    return ((ts.astype("datetime64[ns]") - EPOCH) / SECONDS).to_numpy(dtype="float64")


def interpolate_gps_odometer(txns: pd.DataFrame, pings: pd.DataFrame) -> np.ndarray:
    """Telematics odometer at each swipe time (NaN if the truck has no pings around that moment)."""
    result = np.full(len(txns), np.nan)
    usable = pings.dropna(subset=["odometer"])
    for truck_id, rows in txns.groupby("truck_id").groups.items():
        truck_pings = usable[usable["truck_id"] == truck_id].sort_values("ts_local")
        if len(truck_pings) < 2:
            continue
        xp = _seconds(truck_pings["ts_local"])
        x = _seconds(txns.loc[rows, "ts_local"])
        inside = (x >= xp[0]) & (x <= xp[-1])
        values = np.interp(x, xp, truck_pings["odometer"].to_numpy(dtype="float64"))
        positions = txns.index.get_indexer(rows)
        result[positions] = np.where(inside, values, np.nan)
    return result


def repair_odometers(
    txns: pd.DataFrame, pings: pd.DataFrame, max_gap_mi: float
) -> tuple[pd.DataFrame, OdometerStats]:
    """Return swipes with a repaired `odometer` and an `odometer_repaired_flag`.

    Needs txns columns: truck_id, ts_local, odometer_raw. Needs pings columns: truck_id, ts_local, odometer.
    A swipe the GPS cannot cover keeps its card value (or stays blank): we never invent a number.
    """
    out = txns.reset_index(drop=True).copy()
    gps = interpolate_gps_odometer(out, pings)
    raw = out["odometer_raw"].to_numpy(dtype="float64")
    blank = np.isnan(raw)
    wrong = ~blank & ((raw <= 0) | (np.abs(raw - gps) > max_gap_mi))  # NaN gps compares False
    can_repair = (blank | wrong) & ~np.isnan(gps)
    out["odometer"] = np.where(can_repair, np.round(gps, ODOMETER_DECIMALS), raw)
    out["odometer_repaired_flag"] = can_repair
    stats = OdometerStats(
        blank=int(blank.sum()),
        wrong=int(wrong.sum()),
        repaired=int(can_repair.sum()),
        unrepairable=int(((blank | wrong) & ~can_repair).sum()),
    )
    return out, stats

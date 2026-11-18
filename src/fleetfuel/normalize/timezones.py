"""Timezones: put every timestamp on Arizona local time (naive), and sanity-check the result.

Arizona has no daylight saving time, but the GPS vendor exports UTC. Each source has a
documented timezone (`pipeline.source_timezones`); this step converts all of them to the
hub's timezone (`period.timezone`) and drops the offset marker, as the contract requires.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

NS_UNIT = "datetime64[ns]"
SECONDS_PER_MINUTE = 60


def to_local(series: pd.Series, source_tz: str, target_tz: str) -> pd.Series:
    """Convert naive timestamps recorded in `source_tz` to naive timestamps in `target_tz`.

    Times that repeat when clocks go back are read as standard time (a source that
    observes daylight saving is not expected in this project, so this is a safe default).
    """
    stamps = series.astype(NS_UNIT)
    if source_tz == target_tz:
        return stamps
    standard_time = np.zeros(len(stamps), dtype=bool)
    aware = stamps.dt.tz_localize(source_tz, ambiguous=standard_time, nonexistent="shift_forward")
    return aware.dt.tz_convert(target_tz).dt.tz_localize(None)


def count_shifted(before: pd.Series, after: pd.Series) -> int:
    """How many timestamps changed when converted."""
    return int((before.astype(NS_UNIT) != after.astype(NS_UNIT)).sum())


@dataclass
class StartSanity:
    """Result of comparing the first GPS ping of a truck-day with the planned route start."""

    compared: int
    flagged: int
    median_diff_minutes: float | None
    max_abs_diff_minutes: float | None

    def note(self, tolerance_minutes: float) -> str:
        """One plain-English sentence for the Data hub page."""
        if not self.compared:
            return "No truck-days could be compared between the first GPS ping and the planned route start."
        return (
            f"Compared {self.compared} truck-days: the first GPS ping is a median of {self.median_diff_minutes:.0f} minutes "
            f"from the planned route start, and {self.flagged} are more than {tolerance_minutes:g} minutes apart."
        )


def start_time_sanity(
    pings_local: pd.DataFrame, plans_local: pd.DataFrame, tolerance_minutes: float
) -> StartSanity:
    """Compare each truck-day's first ping with its planned start (stop 0 of the route plan).

    A large, steady gap (for example 7 hours) means a timezone was converted wrongly; a few
    large gaps usually mean a GPS outage at the start of a day.
    """
    starts = plans_local[plans_local["stop_seq"] == 0][["truck_id", "planned_ts_local"]].copy()
    starts["day"] = starts["planned_ts_local"].dt.normalize()
    starts = starts.groupby(["truck_id", "day"], as_index=False)["planned_ts_local"].min()

    first = pings_local.assign(day=pings_local["ts_local"].dt.normalize()).groupby(["truck_id", "day"], as_index=False)[
        "ts_local"
    ].min()
    both = starts.merge(first, on=["truck_id", "day"], how="inner")
    if both.empty:
        return StartSanity(0, 0, None, None)
    diff = (both["ts_local"] - both["planned_ts_local"]).dt.total_seconds() / SECONDS_PER_MINUTE
    return StartSanity(
        compared=len(both),
        flagged=int((diff.abs() > tolerance_minutes).sum()),
        median_diff_minutes=float(diff.median()),
        max_abs_diff_minutes=float(diff.abs().max()),
    )

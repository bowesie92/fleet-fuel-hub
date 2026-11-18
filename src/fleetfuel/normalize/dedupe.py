"""Duplicate swipes: remove a second card swipe that repeats a fill already recorded.

Business rule: the same truck, within a couple of minutes, for the same dollar amount, is
one fill swiped twice. We keep the first swipe and record every removed row (and which
row it duplicated) in `dq_duplicate`, so nothing disappears without a trace.
"""

from __future__ import annotations

import pandas as pd

SECONDS_PER_MINUTE = 60
FLOAT_SLACK = 1e-9  # guards a tolerance edge case such as 0.01 against floating point noise


def remove_duplicates(
    txns: pd.DataFrame, window_minutes: float, amount_tolerance_usd: float
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split swipes into (kept, removed). `removed` has source_row, kept_source_row and reason.

    Needs columns: source_row, truck_id, ts_local, total.
    """
    cols = ["source_row", "truck_id", "ts_local", "total"]
    rows = txns[cols].reset_index(drop=True)
    pairs = rows.merge(rows, on="truck_id", suffixes=("_a", "_b"))
    earlier = (pairs["ts_local_a"] < pairs["ts_local_b"]) | (
        (pairs["ts_local_a"] == pairs["ts_local_b"]) & (pairs["source_row_a"] < pairs["source_row_b"])
    )
    gap_seconds = (pairs["ts_local_b"] - pairs["ts_local_a"]).dt.total_seconds()
    close_in_time = gap_seconds <= window_minutes * SECONDS_PER_MINUTE + FLOAT_SLACK
    same_amount = (pairs["total_a"] - pairs["total_b"]).abs() <= amount_tolerance_usd + FLOAT_SLACK
    dup_pairs = pairs[earlier & close_in_time & same_amount]

    # For each later swipe, point at the earliest swipe it repeats, then follow the chain to its root.
    first_original = (
        dup_pairs.sort_values(["ts_local_a", "source_row_a"]).groupby("source_row_b")["source_row_a"].first().to_dict()
    )

    def root(row: int) -> int:
        while row in first_original:
            row = first_original[row]
        return row

    removed = pd.DataFrame(
        {
            "source_row": list(first_original),
            "kept_source_row": [root(r) for r in first_original],
        }
    )
    removed["reason"] = (
        f"same truck, within {window_minutes:g} minutes, total within ${amount_tolerance_usd:.2f} of an earlier swipe"
    )
    removed = removed.astype({"source_row": "int64", "kept_source_row": "int64"}).sort_values("source_row").reset_index(drop=True)
    kept = txns[~txns["source_row"].isin(removed["source_row"])].reset_index(drop=True)
    return kept, removed

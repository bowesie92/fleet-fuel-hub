"""Accuracy scoring: compare the cleaned hub with the generator's ground truth.

This is the only module that reads `ground_truth/`. The cleaning steps never see it. If the
folder is missing (for example with real data), nothing is scored and nothing is written.
Metric definitions are in docs/contracts.md section 6.

How dedupe is scored (open question D8, decision pending): the contract says "removed rows that are
true duplicates". Here a removal counts as correct when the row belongs to a real fill that was
swiped more than once, so removing either copy of an identical pair is correct (the two swipes
carry the same data). The stricter reading, which only credits removing the row flagged
`is_duplicate` in the truth file, would turn "kept the injected copy, removed the original" into a
false positive. If D8 picks the strict reading, only `score_dedupe` changes.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from fleetfuel import db

TRUTH_FILE = "fuel_txn_truth.csv"
NO_ODOMETER_ISSUE = "none"


def _ratio(numerator: int, denominator: int) -> float | None:
    """numerator / denominator, or None when there were no cases (never a made-up perfect score)."""
    return float(numerator / denominator) if denominator else None


def _row(metric: str, tp: int, fp: int, fn: int, value: float | None, notes: str) -> dict:
    return {"metric": metric, "value": value, "true_positive": tp, "false_positive": fp, "false_negative": fn, "notes": notes}


def score_station_match(facts: pd.DataFrame, truth: pd.DataFrame) -> list[dict]:
    """Precision and recall of station matching, per the contract definitions."""
    both = facts.merge(truth[["source_row", "true_station_id"]], on="source_row", how="left")
    matched = both["station_id"].notna()
    correct = matched & (both["station_id"] == both["true_station_id"])
    true_rows = int((~truth["is_duplicate"]).sum())
    tp, matched_n = int(correct.sum()), int(matched.sum())
    return [
        _row(
            "station_match_precision", tp, matched_n - tp, true_rows - tp, _ratio(tp, matched_n),
            f"{tp} of {matched_n} matched swipes went to the correct station.",
        ),
        _row(
            "station_match_recall", tp, matched_n - tp, true_rows - tp, _ratio(tp, true_rows),
            f"{tp} of {true_rows} real fills were matched to the correct station.",
        ),
    ]


def score_dedupe(removed: pd.DataFrame, truth: pd.DataFrame) -> list[dict]:
    """Precision and recall of duplicate removal.

    A removal is correct when the row belongs to a real fill that was swiped more than once; removing
    either copy of a pair counts, because the two swipes are identical.
    """
    event_size = truth.groupby("true_event_id")["source_row"].transform("size")
    extras = (
        truth.assign(extras=event_size - 1).drop_duplicates("true_event_id").set_index("true_event_id")["extras"]
    )
    removed_events = removed.merge(truth[["source_row", "true_event_id"]], on="source_row", how="left")
    removed_per_event = removed_events.groupby("true_event_id").size()
    tp = int(sum(min(count, int(extras.get(event, 0))) for event, count in removed_per_event.items()))
    removed_n, true_dups = len(removed), int(extras.sum())
    return [
        _row(
            "dedupe_precision", tp, removed_n - tp, true_dups - tp, _ratio(tp, removed_n),
            f"{tp} of {removed_n} removed swipes were real duplicates.",
        ),
        _row(
            "dedupe_recall", tp, removed_n - tp, true_dups - tp, _ratio(tp, true_dups),
            f"{tp} of {true_dups} real duplicate swipes were removed.",
        ),
    ]


def score_odometer(facts: pd.DataFrame, truth: pd.DataFrame, tolerance_mi: float) -> list[dict]:
    """Share of swipes with an odometer problem whose final odometer is within tolerance of the truth."""
    both = facts.merge(truth[["source_row", "true_odometer", "odometer_issue"]], on="source_row", how="left")
    issue = both["odometer_issue"] != NO_ODOMETER_ISSUE
    close = (both["odometer"] - both["true_odometer"]).abs() <= tolerance_mi
    tp = int((issue & close).sum())
    issue_n = int(issue.sum())
    over_repaired = int((~issue & both["odometer_repaired_flag"]).sum())
    return [
        _row(
            "odometer_repair_accuracy", tp, over_repaired, issue_n - tp, _ratio(tp, issue_n),
            f"{tp} of {issue_n} swipes with a blank or wrong odometer ended within {tolerance_mi:g} miles of the truth; "
            f"{over_repaired} good readings were changed.",
        )
    ]


def evaluate(con, truth_dir: Path, odometer_tolerance_mi: float) -> int:
    """Score the hub against ground truth and write eval_accuracy. Returns the number of metrics written (0 if no truth)."""
    truth_path = Path(truth_dir) / TRUTH_FILE
    if not truth_path.is_file():
        return 0
    truth = pd.read_csv(truth_path)
    truth["is_duplicate"] = truth["is_duplicate"].astype(str).str.lower().isin(["true", "1"])
    facts = db.read_table(con, "fact_fuel_txn")
    removed = db.read_table(con, "dq_duplicate")
    rows = score_station_match(facts, truth) + score_dedupe(removed, truth) + score_odometer(facts, truth, odometer_tolerance_mi)
    scored = [r for r in rows if r["value"] is not None]
    for r in rows:
        if r["value"] is None:
            db.log_dq(
                con, f"eval_{r['metric']}", 0, 0, 0,
                f"{r['metric']} was not scored because there were no cases to score (its denominator was zero).",
            )
    db.insert_frame(con, "eval_accuracy", pd.DataFrame(scored))
    return len(scored)

"""Shared checks for every ingest adapter: file exists, required columns present, rejects counted.

Business rule: a row that cannot be understood is never silently dropped. Each adapter
returns a `LoadResult` that says how many rows came in, how many were set aside and why,
and `ingest.run` writes that into `dq_log`.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd


class SourceError(ValueError):
    """A source file is missing or does not have the columns the contract promises."""


@dataclass
class LoadResult:
    """What an adapter produced: the clean rows plus an honest count of what was set aside."""

    source: str
    df: pd.DataFrame
    rows_in: int
    rejected: int = 0
    reasons: Counter = field(default_factory=Counter)
    extra_issues: int = 0  # issues that did not remove a row (for example a blank odometer)
    extra_notes: list[str] = field(default_factory=list)

    @property
    def rows_out(self) -> int:
        """Rows that passed validation."""
        return len(self.df)

    @property
    def issue_count(self) -> int:
        """Rejected rows plus other issues that were fixed or flagged along the way."""
        return self.rejected + self.extra_issues

    def note(self) -> str:
        """One plain-English sentence for the Data hub page."""
        text = f"Read {self.rows_in} rows from {self.source}"
        if self.rejected:
            detail = ", ".join(f"{count} {reason}" for reason, count in self.reasons.most_common())
            text += f"; {self.rejected} could not be used and were set aside ({detail})"
        else:
            text += "; every row parsed"
        for extra in self.extra_notes:
            text += f"; {extra}"
        return text + "."


def read_source_csv(path: Path, required: list[str], source: str) -> pd.DataFrame:
    """Read a CSV as text and check every required column exists, with a clear error if not."""
    path = Path(path)
    if not path.is_file():
        raise SourceError(f"{source} not found at {path}. Generate or fetch the raw data first.")
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [str(c).strip() for c in df.columns]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise SourceError(f"{source} ({path.name}) is missing required columns: {', '.join(missing)}")
    return df


def to_number(series: pd.Series) -> pd.Series:
    """Parse text to numbers, turning anything unreadable into NaN (thousands commas are tolerated)."""
    cleaned = series.astype(str).str.strip().str.replace(",", "", regex=False)
    return pd.to_numeric(cleaned, errors="coerce")


def blank(series: pd.Series) -> pd.Series:
    """True where a text cell is empty or only spaces."""
    return series.astype(str).str.strip() == ""


def split_rejects(
    df: pd.DataFrame, checks: list[tuple[str, pd.Series]]
) -> tuple[pd.DataFrame, pd.DataFrame, Counter]:
    """Apply (reason, bad_mask) checks in order. Each bad row is counted once, under its first reason.

    Returns (good rows, rejected rows with a `reject_reason` column, reason counts).
    """
    reason = pd.Series("", index=df.index, dtype="object")
    counts: Counter = Counter()
    for label, bad in checks:
        fresh = bad & (reason == "")
        reason[fresh] = label
        counts[label] = int(fresh.sum())
    counts = Counter({k: v for k, v in counts.items() if v})
    rejected = df[reason != ""].copy()
    rejected["reject_reason"] = reason[reason != ""]
    return df[reason == ""].copy(), rejected, counts

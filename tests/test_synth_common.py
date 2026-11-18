"""Shared helpers for the synth tests (no tests here): run the generator on fixtures once and reuse it.

Every run points raw_dir at an empty folder, so the small fixtures in tests/fixtures/synth/ are
always used, even after the real inputs exist in data/raw/. Output goes to a temp folder.
"""

from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

import pandas as pd

from fleetfuel import config, synth
from fleetfuel.synth.inputs import FIXTURE_DIR

FIXTURE_PARTNER_BBOX = [32.04, -111.01, 32.19, -110.89]  # south, west, north, east: Tucson-side fixture stations
OVERRIDES = FIXTURE_DIR / "station_overrides.yaml"

_TMP = tempfile.TemporaryDirectory(prefix="synth_tests_")
_RUNS: dict[str, Path] = {}


def tiny_cfg() -> dict:
    """The real assumptions shrunk to a three-week, small-fleet world so a run takes a moment."""
    cfg = copy.deepcopy(config.load_assumptions())
    cfg["period"]["start_date"] = "2025-11-03"
    cfg["period"]["end_date"] = "2025-11-23"
    cfg["fleet"]["linehaul_trucks"] = 2
    cfg["fleet"]["local_trucks"] = 4
    cfg["routes"]["local_route_count"] = 3
    cfg["routes"]["linehaul_runs_per_day"] = 2
    cfg["prices"]["partner_set_bbox"] = FIXTURE_PARTNER_BBOX
    return cfg


def full_cfg() -> dict:
    """The real assumptions unchanged: twelve months, 20 trucks, 10 routes."""
    cfg = copy.deepcopy(config.load_assumptions())
    cfg["prices"]["partner_set_bbox"] = FIXTURE_PARTNER_BBOX
    return cfg


def run_once(name: str, cfg: dict) -> Path:
    """Run the generator into a named temp folder (once per process) and return that folder."""
    if name not in _RUNS:
        out = Path(_TMP.name) / name
        synth.run(cfg, raw_dir=Path(_TMP.name) / "no_real_inputs", out_dir=out, overrides_path=OVERRIDES)
        _RUNS[name] = out
    return _RUNS[name]


def load(out: Path) -> dict[str, pd.DataFrame]:
    """Read every output CSV as text (so formats can be checked exactly)."""
    names = ["fleet", "partner_program", "station_prices", "fuel_card", "telematics", "route_plans"]
    data = {n: pd.read_csv(out / f"{n}.csv", dtype=str, keep_default_na=False) for n in names}
    gt = out / "ground_truth"
    data["truth"] = pd.read_csv(gt / "fuel_txn_truth.csv", dtype=str, keep_default_na=False)
    data["issues"] = pd.read_csv(gt / "injected_issues.csv", dtype=str, keep_default_na=False)
    data["manifest"] = json.loads((gt / "run_manifest.json").read_text(encoding="utf-8"))
    return data

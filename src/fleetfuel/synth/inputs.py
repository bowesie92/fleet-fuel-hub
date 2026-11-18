"""synth/inputs.py: load the real public inputs, or small fixtures when they are missing.

Business terms: the generator builds on three public files (stations, customer points,
weekly diesel prices). Each is taken from data/raw/ when present, otherwise from the
small made-up fixtures under tests/fixtures/synth/. The run manifest records which.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from fleetfuel import config
from fleetfuel.stations import load_stations

FIXTURE_DIR = config.REPO_ROOT / "tests" / "fixtures" / "synth"
STATIONS_FILE = "stations.geojson"
CUSTOMERS_FILE = "customer_points.csv"
EIA_FILE = "eia_diesel.csv"


@dataclass
class Inputs:
    """The three public inputs and where each one came from ('real' or 'fixture')."""

    stations: pd.DataFrame
    customers: pd.DataFrame
    eia: pd.DataFrame
    sources: dict[str, str]


def _pick(raw_dir: Path, fixture_dir: Path, name: str) -> tuple[Path, str]:
    """Prefer the real file in raw_dir; fall back to the fixture copy. Returns (folder, 'real' | 'fixture')."""
    if (raw_dir / name).is_file():
        return raw_dir, "real"
    if not (fixture_dir / name).is_file():
        raise FileNotFoundError(
            f"Input '{name}' is missing from {raw_dir} and there is no fixture copy at {fixture_dir / name}. "
            "Run `uv run fleetfuel fetch` first."
        )
    return fixture_dir, "fixture"


def read_stations(raw_dir: Path, overrides_path: Path | None = None) -> pd.DataFrame:
    """Read stations through fleetfuel.stations.load_stations (manual overrides applied), sorted by station_id."""
    frame, _records = load_stations(raw_dir, overrides_path)
    keep = ["station_id", "name", "brand", "city", "station_class", "truck_access", "lat", "lon"]
    return frame[keep].astype({"lat": float, "lon": float}).reset_index(drop=True)


def read_customers(path: Path) -> pd.DataFrame:
    """Read customer_points.csv (contract 4.3) sorted by customer_id."""
    frame = pd.read_csv(path, dtype={"customer_id": str})
    return frame.sort_values("customer_id").reset_index(drop=True)


def read_eia(path: Path) -> pd.DataFrame:
    """Read eia_diesel.csv (contract 4.1) with week_start as a real date, sorted ascending."""
    frame = pd.read_csv(path, dtype={"week_start": str})
    frame["week_start"] = pd.to_datetime(frame["week_start"]).dt.date
    return frame.sort_values("week_start").reset_index(drop=True)


def load_inputs(raw_dir: Path, fixture_dir: Path = FIXTURE_DIR, overrides_path: Path | None = None) -> Inputs:
    """Load stations, customers and EIA prices, real where available, fixture otherwise."""
    st_dir, st_src = _pick(raw_dir, fixture_dir, STATIONS_FILE)
    cu_dir, cu_src = _pick(raw_dir, fixture_dir, CUSTOMERS_FILE)
    ei_dir, ei_src = _pick(raw_dir, fixture_dir, EIA_FILE)
    if Path(raw_dir).resolve() == config.RAW_DIR.resolve():
        for name, src in ((STATIONS_FILE, st_src), (CUSTOMERS_FILE, cu_src), (EIA_FILE, ei_src)):
            if src == "fixture":
                print(f"[synth] WARNING: {name} is missing from {raw_dir}; made-up fixtures were used. "
                      "Run `uv run fleetfuel fetch` for real inputs.")
    return Inputs(
        stations=read_stations(st_dir, overrides_path),
        customers=read_customers(cu_dir / CUSTOMERS_FILE),
        eia=read_eia(ei_dir / EIA_FILE),
        sources={"stations": st_src, "customers": cu_src, "eia": ei_src},
    )

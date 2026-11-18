"""Phoenix DC on-site fueling (contract 4.12): yard fills are tank-log rows, never card swipes."""

from __future__ import annotations

import copy
from datetime import date

import pandas as pd
from test_synth_common import full_cfg, load, run_once, tiny_cfg

from fleetfuel.synth import inputs, prices
from fleetfuel.synth.params import load_params

COLUMNS = ["FillDate", "FillTime", "Yard", "Unit", "Gallons", "PricePerGal", "Total"]
PRICE_TOLERANCE = 0.0006  # three-decimal rounding of both sides
SKIP_RATE_TOLERANCE = 0.02  # how far the observed skipped share may sit from the configured rate
TOTAL_TOLERANCE = 0.006  # half a cent of rounding on each side


def _yard(name: str, cfg: dict) -> pd.DataFrame:
    out = run_once(name, cfg)
    return pd.read_csv(out / "yard_fuel.csv", dtype=str, keep_default_na=False)


def test_columns_and_only_the_phoenix_tank():
    yard = _yard("yard_full", full_cfg())
    assert list(yard.columns) == COLUMNS
    assert set(yard["Yard"]) == {"PHX_DC"}
    assert yard["Unit"].str.startswith("PHX-").all()
    assert (yard["Gallons"].str.split(".").str[1].str.len() == 3).all()
    key = list(zip(yard["FillDate"], yard["FillTime"], yard["Unit"]))
    assert key == sorted(key)


def test_total_is_gallons_times_price():
    yard = _yard("yard_full", full_cfg())
    gal, ppg, tot = (yard[c].astype(float) for c in ("Gallons", "PricePerGal", "Total"))
    assert ((gal * ppg - tot).abs() <= TOTAL_TOLERANCE).all()


def test_price_is_eia_minus_discount():
    cfg = full_cfg()
    p = load_params(cfg)
    yard = _yard("yard_full", cfg)
    eia = pd.read_csv(inputs.FIXTURE_DIR / "eia_diesel.csv")
    days = [date.fromisoformat(d) for d in yard["FillDate"]]
    weekly, _, _ = prices.eia_price_for_days(eia, days)
    expected = weekly - p.yard_discount_per_gal
    assert (abs(yard["PricePerGal"].astype(float).to_numpy() - expected) <= PRICE_TOLERANCE).all()


def test_skip_rate_within_tolerance():
    cfg = full_cfg()
    p = load_params(cfg)
    plans = load(run_once("yard_full", cfg))["route_plans"]
    departures = plans[(plans["StopSeq"] == "0") & plans["RouteID"].str.startswith("PHX-L")]
    skipped = 1 - len(_yard("yard_full", cfg)) / len(departures)
    assert abs(skipped - p.yard_skip_rate) < SKIP_RATE_TOLERANCE


def test_no_card_fill_at_departure_when_the_yard_fill_happens():
    cfg = full_cfg()
    p = load_params(cfg)
    data = load(run_once("yard_full", cfg))
    yard = _yard("yard_full", cfg)
    fill_ts = pd.to_datetime(yard["FillDate"] + " " + yard["FillTime"])
    truth = data["truth"].assign(ts=pd.to_datetime(data["truth"]["true_ts_local"]))
    window = pd.Timedelta(minutes=2 * p.fill_minutes)
    for unit, ts in zip(yard["Unit"], fill_ts):
        rows = truth[(truth["truck_id"] == unit) & (truth["ts"] >= ts) & (truth["ts"] < ts + window)]
        assert rows.empty


def test_yard_rows_never_in_card_file_or_ground_truth():
    out = run_once("yard_full", full_cfg())
    data = load(out)
    assert "yard_fuel.csv" not in set(data["issues"]["source_file"])
    assert len(data["truth"]) == len(data["fuel_card"])
    assert data["manifest"]["row_counts"]["yard_fuel.csv"] == len(_yard("yard_full", full_cfg()))
    assert "PHX_DC" not in (out / "fuel_card.csv").read_text(encoding="utf-8")


def test_no_yard_fills_when_onsite_fuel_is_false():
    cfg = copy.deepcopy(tiny_cfg())
    cfg["yards"]["phoenix_dc"]["onsite_fuel"] = False
    off = run_once("yard_off", cfg)
    on = run_once("yard_on", tiny_cfg())
    assert len(pd.read_csv(off / "yard_fuel.csv")) == 0
    assert len(pd.read_csv(on / "yard_fuel.csv")) > 0
    assert len(pd.read_csv(off / "fuel_card.csv")) > len(pd.read_csv(on / "fuel_card.csv"))

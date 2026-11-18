"""Repo rules for generated data: anonymous customers, no invented real names, no em dashes, no secrets."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from test_synth_common import OVERRIDES, full_cfg, load, run_once

from fleetfuel.synth import inputs
from fleetfuel.synth.messiness import MERCHANT_CODE_PREFIX

EM_DASH = chr(0x2014)
SYNTH_SRC = Path(inputs.__file__).parent


@pytest.fixture(scope="module")
def out():
    return run_once("full", full_cfg())


def words(text: str) -> set[str]:
    """Upper-case alphabetic words in a text."""
    return set(re.findall(r"[A-Z]+", text.upper()))


def test_merchant_names_only_use_words_from_the_station_file(out):
    """Merchant names come only from the stations input (here: made-up fixtures), never from anywhere else."""
    stations = inputs.read_stations(inputs.FIXTURE_DIR, OVERRIDES)
    allowed = {MERCHANT_CODE_PREFIX, *words(full_cfg()["messiness"]["unnamed_station_merchant_label"])}
    for col in ("name", "brand", "city"):
        for value in stations[col].dropna():
            allowed |= words(value)
    seen = set()
    for name in load(out)["fuel_card"]["MerchantName"]:
        seen |= words(name)
    assert seen <= allowed, seen - allowed


def test_customers_are_only_anonymous_ids(out):
    plans = load(out)["route_plans"]
    ids = plans.loc[plans["StopType"] == "customer", "LocationID"]
    assert ids.map(lambda v: re.fullmatch(r"CUST-\d{4}", v) is not None).all()
    customers = inputs.read_customers(inputs.FIXTURE_DIR / "customer_points.csv")
    assert set(ids) <= set(customers["customer_id"])
    assert list(customers.columns) == ["customer_id", "lat", "lon"]


def test_fixture_station_names_are_made_up():
    stations = inputs.read_stations(inputs.FIXTURE_DIR, OVERRIDES)
    named = stations["name"].dropna()
    assert len(named) > 0
    for name in named:
        assert re.search(r"Sample|Pump|Gas|Fuel|Side|Road", name), name


def test_no_em_dashes_in_outputs_or_source(out):
    for path in [*out.rglob("*.csv"), *out.rglob("*.json"), *SYNTH_SRC.glob("*.py")]:
        assert EM_DASH not in path.read_text(encoding="utf-8"), path.name


def test_telematics_file_is_reasonably_small(out):
    assert (out / "telematics.csv").stat().st_size < 25 * 1024 * 1024


def test_vendor_feed_never_shows_internal_station_ids(out):
    names = load(out)["fuel_card"]["MerchantName"]
    assert not names.str.contains(r"STN|Station \d|STATION \d", regex=True).any()
    label = full_cfg()["messiness"]["unnamed_station_merchant_label"]
    truth = load(out)["truth"]
    stations = inputs.read_stations(inputs.FIXTURE_DIR, OVERRIDES)
    unnamed = set(stations.loc[stations["name"].isna() & stations["brand"].isna(), "station_id"])
    card = load(out)["fuel_card"]
    rows = card[truth["true_station_id"].isin(unnamed) & (truth["name_variant"].isin(["canonical", "upper_store"]))]
    assert len(rows) > 0 or not unnamed
    for station_id in unnamed:
        shown = card.loc[(truth["true_station_id"] == station_id) & truth["name_variant"].isin(["canonical", "upper_store"]),
                         "MerchantName"]
        assert shown.nunique() <= 1 and all(n.startswith(label.upper() + " #") for n in shown)


def test_missing_default_inputs_print_a_loud_warning(tmp_path, monkeypatch, capsys):
    from fleetfuel import config

    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    inputs.load_inputs(tmp_path)
    assert capsys.readouterr().out.count("WARNING") == 3
    inputs.load_inputs(tmp_path / "elsewhere")
    assert "WARNING" not in capsys.readouterr().out

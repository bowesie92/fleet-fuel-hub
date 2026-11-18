"""Unit tests for the generator's building blocks: geometry, routes, prices, fueling choices, input fallback."""

from __future__ import annotations

import json
import shutil
from datetime import date

import numpy as np
import pandas as pd
import pytest
from test_synth_common import _TMP, OVERRIDES, full_cfg, load, run_once, tiny_cfg

from fleetfuel import synth
from fleetfuel.synth import inputs, messiness, prices, routes
from fleetfuel.synth.geo import haversine_mi, segment_distance_mi
from fleetfuel.synth.params import load_params


def test_haversine_known_distance():
    # One degree of latitude is about 69 miles.
    assert haversine_mi(32.0, -111.0, 33.0, -111.0) == pytest.approx(69.09, abs=0.1)


def test_segment_distance_to_midpoint_and_beyond_the_end():
    assert segment_distance_mi(32.5, -111.0, 32.0, -111.0, 33.0, -111.0) == pytest.approx(0.0, abs=1e-6)
    beyond = segment_distance_mi(34.0, -111.0, 32.0, -111.0, 33.0, -111.0)
    assert beyond == pytest.approx(haversine_mi(34.0, -111.0, 33.0, -111.0), abs=0.2)


def test_eia_price_lookup_carries_forward_and_backfills():
    eia = pd.DataFrame({"week_start": [date(2025, 11, 3), date(2025, 11, 10)], "price_per_gal": [4.0, 4.5]})
    days = [date(2025, 11, 1), date(2025, 11, 3), date(2025, 11, 9), date(2025, 11, 10), date(2025, 11, 16), date(2025, 11, 17)]
    price, carried, before = prices.eia_price_for_days(eia, days)
    assert list(price) == [4.0, 4.0, 4.0, 4.5, 4.5, 4.5]
    assert carried == 1 and before == 1


def test_allocate_sizes_respects_limits_and_explains_shortages():
    rng = np.random.default_rng(1)
    sizes = routes.allocate_sizes(10, 90, 8, 12, rng)
    assert sizes.sum() <= 90 and sizes.min() >= 8 and sizes.max() <= 12
    with pytest.raises(ValueError, match="customer points"):
        routes.allocate_sizes(10, 50, 8, 12, rng)


def test_local_routes_are_geographically_compact():
    p = load_params(full_cfg())
    customers = inputs.read_customers(inputs.FIXTURE_DIR / "customer_points.csv")
    built = routes.build_local_routes(p, customers, np.random.default_rng(3))
    assert len(built) == 10
    used = [s.location_id for r in built for s in r.stops if s.stop_type == "customer"]
    assert len(used) == len(set(used))
    leg = np.mean([s.miles_from_prev for r in built for s in r.stops[1:-1]])
    random_pair = float(np.mean(haversine_mi(customers["lat"].to_numpy()[:, None], customers["lon"].to_numpy()[:, None],
                                             customers["lat"].to_numpy()[None, :], customers["lon"].to_numpy()[None, :]))) * p.circuity
    assert leg < random_pair * 0.6


def test_merchant_name_variants():
    st = pd.Series({"station_id": "STN-007", "name": "Sample Travel Plaza #12", "brand": "Sample Travel Plaza",
                    "city": "Tucson", "station_class": "travel_center"})
    assert messiness.merchant_name(st, "canonical", 812, "MERCH 004512", "FUEL STOP") == "Sample Travel Plaza #12"
    assert messiness.merchant_name(st, "upper_store", 812, "MERCH 004512", "FUEL STOP") == "SAMPLE TRAVEL PLAZA #812 TUCSON"
    assert messiness.merchant_name(st, "short_brand", 812, "MERCH 004512", "FUEL STOP") == "Sample Travel Plaza"
    assert messiness.merchant_name(st, "merchant_code", 812, "MERCH 004512", "FUEL STOP") == "MERCH 004512"
    blank = pd.Series({"station_id": "STN-007", "name": np.nan, "brand": None, "city": None, "station_class": "unbranded"})
    assert messiness.merchant_name(blank, "canonical", 4471, "MERCH 000001", "FUEL STOP") == "FUEL STOP #4471"
    assert messiness.merchant_name(blank, "upper_store", 4471, "MERCH 000001", "FUEL STOP") == "FUEL STOP #4471"
    assert messiness.merchant_name(blank, "short_brand", 4471, "MERCH 000001", "FUEL STOP") == "FUEL STOP"
    assert messiness.merchant_name(blank, "merchant_code", 4471, "MERCH 000001", "FUEL STOP") == "MERCH 000001"


def test_wrong_odometer_is_always_clearly_wrong():
    rng = np.random.default_rng(5)
    for true_value in (140_318, 99_999, 450_001, 120_000):
        for _ in range(200):
            assert abs(messiness.wrong_odometer(true_value, 25.0, rng, 20, 2) - true_value) > 25.0


def test_sensible_fills_are_cheaper_than_convenient_ones_on_average():
    t = load(run_once("full", full_cfg()))
    truth, card = t["truth"], t["fuel_card"]
    ppg = card["PPG"].astype(float)
    sensible = ppg[(truth["fill_behavior"] == "sensible") & (truth["route_id"].str.startswith("TUC"))].mean()
    other = ppg[(truth["fill_behavior"] != "sensible") & (truth["route_id"].str.startswith("TUC"))].mean()
    assert sensible <= other


def test_prices_follow_eia_plus_offsets():
    cfg = full_cfg()
    t = load(run_once("full", cfg))
    stations = inputs.read_stations(inputs.FIXTURE_DIR, OVERRIDES)
    eia = inputs.read_eia(inputs.FIXTURE_DIR / "eia_diesel.csv")
    posted = t["station_prices"].assign(posted=lambda d: d["posted_price_per_gal"].astype(float))
    posted["date"] = pd.to_datetime(posted["date"]).dt.date
    weeks = np.array([np.datetime64(w) for w in eia["week_start"]], dtype="datetime64[D]")
    idx = np.searchsorted(weeks, np.array([np.datetime64(d) for d in posted["date"]], dtype="datetime64[D]"), side="right") - 1
    posted["eia"] = eia["price_per_gal"].to_numpy()[idx]
    posted = posted.merge(stations[["station_id", "station_class"]], on="station_id")
    posted["resid"] = posted["posted"] - posted["eia"] - posted["station_class"].map(cfg["prices"]["class_offset_per_gal"])
    per_station = posted.groupby("station_id")["resid"].agg(["mean", "std"])
    assert per_station["std"].mean() == pytest.approx(cfg["prices"]["daily_noise_sd_per_gal"], rel=0.15)
    assert per_station["mean"].std() == pytest.approx(cfg["prices"]["station_offset_sd_per_gal"], rel=0.5)


def test_paid_price_is_net_of_partner_discount():
    t = load(run_once("full", full_cfg()))
    partners = dict(zip(t["partner_program"]["station_id"], t["partner_program"]["partner_discount_cpg"].astype(float)))
    posted = t["station_prices"].set_index(["date", "station_id"])["posted_price_per_gal"].astype(float)
    truth, card = t["truth"], t["fuel_card"]
    date_iso = pd.to_datetime(card["TransDate"], format="%m/%d/%Y").dt.strftime("%Y-%m-%d")
    expected = [posted[(d, s)] - partners.get(s, 0.0) / 100.0 for d, s in zip(date_iso, truth["true_station_id"])]
    assert np.abs(np.array(expected) - card["PPG"].astype(float).to_numpy()).max() < 0.0011
    assert any(s in partners for s in truth["true_station_id"])  # some fills happen at partner stations


def test_real_inputs_are_used_when_present_and_recorded_in_the_manifest():
    raw = _TMP_path("real_like_raw")
    raw.mkdir(parents=True, exist_ok=True)
    shutil.copy(inputs.FIXTURE_DIR / "stations.geojson", raw / "stations.geojson")
    out = _TMP_path("real_like_out")
    synth.run(tiny_cfg(), raw_dir=raw, out_dir=out, overrides_path=OVERRIDES)
    manifest = json.loads((out / "ground_truth" / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["inputs"] == {"stations": "real", "customers": "fixture", "eia": "fixture"}


def test_missing_inputs_give_a_plain_english_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="fleetfuel fetch"):
        inputs.load_inputs(tmp_path, tmp_path / "also_empty")


def _TMP_path(name: str):
    from pathlib import Path

    return Path(_TMP.name) / name


def test_added_station_without_osm_ref_gets_name_variants():
    """A chain-locator station (STN-A01, no osm_ref) has a name, so every variant is a real, non-empty string."""
    row = pd.Series({"station_id": "STN-A01", "osm_ref": None, "name": "Sample Cardlock Site", "brand": None,
                     "city": "Tucson", "station_class": "cardlock", "truck_access": "confirmed"})
    for variant in messiness.NAME_VARIANTS:
        text = messiness.merchant_name(row, variant, 214, "MERCH 000001", "Fuel Stop")
        assert isinstance(text, str) and text
    assert messiness.merchant_name(row, "canonical", 214, "MERCH 000001", "Fuel Stop") == "Sample Cardlock Site"
    assert "#214" in messiness.merchant_name(row, "upper_store", 214, "MERCH 000001", "Fuel Stop")

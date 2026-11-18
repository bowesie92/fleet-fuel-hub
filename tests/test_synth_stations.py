"""Station rules (contracts v1.4): truck access, partners, overrides and the cardlock price class."""

from __future__ import annotations

import copy

import pytest
from test_synth_common import FIXTURE_PARTNER_BBOX, OVERRIDES, full_cfg, load, run_once

from fleetfuel.stations import load_stations
from fleetfuel.synth.inputs import FIXTURE_DIR


@pytest.fixture(scope="module")
def data():
    return load(run_once("full", full_cfg()))


@pytest.fixture(scope="module")
def stations():
    frame, records = load_stations(FIXTURE_DIR, OVERRIDES)
    return frame.set_index("station_id"), records


def test_fixture_has_unverified_and_cardlock_stations(stations):
    frame, _ = stations
    assert (frame["truck_access"] == "unverified").sum() >= 1
    assert (frame["station_class"] == "cardlock").sum() >= 1


def test_no_fills_at_unverified_stations_for_any_behavior(data, stations):
    frame, _ = stations
    unverified = set(frame.index[frame["truck_access"] == "unverified"])
    truth = data["truth"]
    assert not set(truth["true_station_id"]) & unverified
    assert set(truth["fill_behavior"]) == {"sensible", "convenient", "odd"}  # odd fills obey the rule too


def test_unverified_stations_are_allowed_when_the_switch_is_on(stations):
    frame, _ = stations
    cfg = copy.deepcopy(full_cfg())
    cfg["fueling"]["use_unverified_truck_access"] = True
    truth = load(run_once("with_unverified", cfg))["truth"]
    unverified = set(frame.index[frame["truck_access"] == "unverified"])
    assert set(truth["true_station_id"]) & unverified


def test_unverified_stations_still_get_posted_prices(data, stations):
    frame, _ = stations
    priced = set(data["station_prices"]["station_id"])
    assert set(frame.index) == priced


def test_partners_are_all_confirmed(data, stations):
    frame, _ = stations
    partners = data["partner_program"]["station_id"]
    assert (frame.loc[partners, "truck_access"] == "confirmed").all()


def test_excluded_stations_never_appear(data, stations):
    _, records = stations
    excluded = [r["station_id"] for r in records if r["action"] == "exclude" and r["status"] == "applied"]
    assert excluded
    for station_id in excluded:
        assert station_id not in set(data["station_prices"]["station_id"])
        assert station_id not in set(data["partner_program"]["station_id"])
        assert station_id not in set(data["truth"]["true_station_id"])


def test_merged_stations_appear_only_under_the_surviving_id(data, stations):
    _, records = stations
    merged = [r for r in records if r["action"] == "merge_into" and r["status"] == "applied"]
    assert merged
    for r in merged:
        gone, kept = r["station_id"], r["target_station_id"]
        ids = set(data["station_prices"]["station_id"])
        assert gone not in ids and kept in ids
        assert gone not in set(data["truth"]["true_station_id"])


def test_cardlock_class_uses_its_own_offset(data, stations):
    import pandas as pd

    frame, _ = stations
    offsets = full_cfg()["prices"]["class_offset_per_gal"]
    prices = data["station_prices"].assign(p=lambda d: d["posted_price_per_gal"].astype(float))
    prices["klass"] = prices["station_id"].map(frame["station_class"])
    by_class = prices.groupby("klass")["p"].mean()
    assert by_class["cardlock"] < by_class["branded"]
    assert offsets["cardlock"] < offsets["unbranded"]
    assert isinstance(by_class, pd.Series)


def _box_stations(frame, bbox):
    south, west, north, east = bbox
    return frame[frame["lat"].between(south, north) & frame["lon"].between(west, east)]


def test_partners_are_exactly_the_confirmed_stations_in_the_box(data):
    frame, _ = load_stations(FIXTURE_DIR, OVERRIDES)
    inside = _box_stations(frame, FIXTURE_PARTNER_BBOX)
    expected = set(inside.loc[inside["truck_access"] == "confirmed", "station_id"])
    assert expected and set(data["partner_program"]["station_id"]) == expected


def test_unverified_stations_in_the_box_are_excluded(data):
    frame, _ = load_stations(FIXTURE_DIR, OVERRIDES)
    inside = _box_stations(frame, FIXTURE_PARTNER_BBOX)
    unverified = set(inside.loc[inside["truck_access"] != "confirmed", "station_id"])
    assert unverified  # the fixture box is built to contain at least one
    assert not unverified & set(data["partner_program"]["station_id"])


def test_an_empty_partner_box_raises():
    cfg = full_cfg()
    cfg["prices"]["partner_set_bbox"] = [0.0, 0.0, 0.1, 0.1]
    with pytest.raises(ValueError, match="partner_set_bbox"):
        run_once("empty_box", cfg)


def _mini_stations():
    import pandas as pd
    return pd.DataFrame({
        "lat": [1.0, 1.0, 1.0, 1.0], "lon": [1.0, 1.0, 1.0, 1.0],
        "truck_access": ["confirmed", "confirmed", "confirmed", "unverified"],
        "name": ["Sample Stop", None, None, "Other Stop"], "brand": [None, "Sample Fuel", None, None],
    })


def test_named_only_excludes_unnamed_stations():
    from fleetfuel.synth.prices import partner_mask
    assert partner_mask(_mini_stations(), (0, 0, 2, 2), True).tolist() == [True, True, False, False]


def test_named_only_false_keeps_unnamed_confirmed_stations():
    from fleetfuel.synth.prices import partner_mask
    assert partner_mask(_mini_stations(), (0, 0, 2, 2), False).tolist() == [True, True, True, False]


def test_named_only_with_only_unnamed_stations_raises():
    from fleetfuel.synth.prices import partner_mask
    with pytest.raises(ValueError, match="partner_set_bbox"):
        partner_mask(_mini_stations().iloc[[2]], (0, 0, 2, 2), True)


def test_missing_named_only_key_stops_the_build_naming_it():
    cfg = full_cfg()
    del cfg["prices"]["partner_set_named_only"]
    with pytest.raises(KeyError, match="prices.partner_set_named_only"):
        run_once("no_named_only_key", cfg)

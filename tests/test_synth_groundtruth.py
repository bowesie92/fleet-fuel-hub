"""Ground truth is complete, consistent with the data, and injected issue rates match the config."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from test_synth_common import OVERRIDES, full_cfg, load, run_once

from fleetfuel.synth.geo import haversine_mi
from fleetfuel.synth.inputs import read_stations
from fleetfuel.synth.inputs import FIXTURE_DIR

SIGMAS = 4  # tolerance in binomial standard deviations for rate checks
MATCH_RADIUS_MI = 1.0  # contract: station matching looks within pipeline.station_match.max_distance_mi
MATCH_WINDOW_MIN = 30
MIN_MATCHABLE_SHARE = 0.95


@pytest.fixture(scope="module")
def data():
    return load(run_once("full", full_cfg()))


def within(observed: int, n: int, rate: float) -> bool:
    """True when `observed` successes out of n are within SIGMAS standard deviations of rate * n."""
    sd = (n * rate * (1 - rate)) ** 0.5
    return abs(observed - n * rate) <= SIGMAS * sd + 1


def test_truth_has_one_row_per_card_row(data):
    card, truth = data["fuel_card"], data["truth"]
    assert len(truth) == len(card)
    assert list(truth["source_row"].astype(int)) == list(range(1, len(card) + 1))
    assert (truth["TransactionID"] == card["TransactionID"]).all()
    assert list(truth.columns) == ["source_row", "TransactionID", "true_event_id", "is_duplicate", "truck_id",
                                   "true_station_id", "true_ts_local", "true_odometer", "odometer_issue",
                                   "name_variant", "fill_behavior", "route_id"]
    assert (truth["truck_id"] == card["Unit"]).all()
    assert set(truth["is_duplicate"]) <= {"true", "false"}


def test_duplicates_share_event_and_fields(data):
    card, truth = data["fuel_card"], data["truth"]
    dups = truth[truth["is_duplicate"] == "true"]
    assert len(dups) > 0
    firsts = truth[truth["is_duplicate"] == "false"].set_index("true_event_id")
    for row in dups.itertuples():
        orig = firsts.loc[row.true_event_id]
        a, b = card.iloc[int(row.source_row) - 1], card.iloc[int(orig["source_row"]) - 1]
        assert a["TransactionID"] != b["TransactionID"]
        same = [c for c in card.columns if c != "TransactionID"]
        assert (a[same] == b[same]).all()
    assert truth.loc[truth["is_duplicate"] == "false", "true_event_id"].is_unique


def test_issue_rows_match_the_truth_table(data):
    truth, issues = data["truth"], data["issues"]
    assert list(issues.columns) == ["issue_id", "issue_type", "source_file", "source_row", "truck_id",
                                    "start_ts_local", "end_ts_local", "details"]
    assert list(issues["issue_id"]) == [f"ISS-{i:05d}" for i in range(1, len(issues) + 1)]
    counts = issues["issue_type"].value_counts()
    assert counts["duplicate_swipe"] == (truth["is_duplicate"] == "true").sum()
    assert counts["missing_odometer"] == (truth["odometer_issue"] == "missing").sum()
    assert counts["wrong_odometer"] == (truth["odometer_issue"] == "wrong").sum()
    assert counts["name_variant"] == (truth["name_variant"] != "canonical").sum()
    assert counts["irregular_ping"] == 1 and counts["timezone_utc"] == 1 and counts["gps_gap"] > 0
    assert set(issues["issue_type"]) == {"name_variant", "duplicate_swipe", "missing_odometer", "wrong_odometer",
                                         "irregular_ping", "gps_gap", "timezone_utc"}
    row_level = issues[issues["source_file"] == "fuel_card.csv"]
    assert (row_level["source_row"].astype(int).between(1, len(truth))).all()
    gaps = issues[issues["issue_type"] == "gps_gap"]
    assert (gaps["start_ts_local"] < gaps["end_ts_local"]).all() and (gaps["truck_id"] != "").all()


def test_manifest(data):
    m = data["manifest"]
    assert m["seed"] == full_cfg()["seed"]
    assert m["period_start"] == "2025-11-01" and m["period_end"] == "2026-10-31"
    assert m["inputs"] == {"stations": "fixture", "customers": "fixture", "eia": "fixture"}
    assert m["row_counts"]["fuel_card.csv"] == len(data["fuel_card"])
    assert m["row_counts"]["telematics.csv"] == len(data["telematics"])
    assert m["row_counts"]["ground_truth/injected_issues.csv"] == len(data["issues"])


def test_injected_rates_match_config(data):
    cfg = full_cfg()["messiness"]
    truth = data["truth"]
    originals = truth[truth["is_duplicate"] == "false"]
    n = len(originals)
    assert within((truth["is_duplicate"] == "true").sum(), n, cfg["duplicate_swipe_rate"])
    assert within((originals["odometer_issue"] == "missing").sum(), n, cfg["missing_odometer_rate"])
    assert within((originals["odometer_issue"] == "wrong").sum(), n, cfg["wrong_odometer_rate"])
    for variant, share in cfg["name_variant_mix"].items():
        assert within((originals["name_variant"] == variant).sum(), n, share), variant
    for behavior, share in full_cfg()["fueling"]["behavior_mix"].items():
        assert within((originals["fill_behavior"] == behavior).sum(), n, share), behavior


def test_gps_gap_rate(data):
    cfg = full_cfg()["messiness"]
    truck_days = len(data["route_plans"].query("StopSeq == '0'"))
    gaps = (data["issues"]["issue_type"] == "gps_gap").sum()
    assert within(gaps, truck_days, cfg["gps_gap_rate_per_truck_day"])
    g = data["issues"].query("issue_type == 'gps_gap'")
    minutes = (pd.to_datetime(g["end_ts_local"]) - pd.to_datetime(g["start_ts_local"])).dt.total_seconds() / 60
    assert minutes.min() >= cfg["gps_gap_minutes_min"]


def test_pings_are_irregular(data):
    t = data["telematics"]
    one = t[t["device_id"] == t["device_id"].iloc[0]]
    gaps = pd.to_datetime(one["event_time"]).diff().dt.total_seconds().dropna()
    assert gaps.nunique() > 20  # many different spacings, not a fixed interval


def test_odometer_values(data):
    card, truth = data["fuel_card"], data["truth"]
    true_odo = truth["true_odometer"].astype(float)
    min_gap = full_cfg()["pipeline"]["odometer"]["max_gap_vs_telematics_mi"]
    for issue, mask in {"none": truth["odometer_issue"] == "none", "missing": truth["odometer_issue"] == "missing",
                        "wrong": truth["odometer_issue"] == "wrong"}.items():
        shown = card.loc[mask, "Odometer"]
        if issue == "missing":
            assert (shown == "").all()
        else:
            diff = (shown.astype(float) - true_odo[mask]).abs()
            assert (diff <= 0.5 + 1e-9).all() if issue == "none" else (diff > min_gap).all()


def test_nearest_ping_is_near_the_true_station(data):
    """A matcher can find the station: the ping nearest each swipe is within the match radius of it."""
    stations = read_stations(FIXTURE_DIR, OVERRIDES).set_index("station_id")
    fleet = data["fleet"].set_index("truck_id")["telematics_device_id"]
    tel = data["telematics"].copy()
    local = pd.to_datetime(tel["event_time"]).dt.tz_localize("UTC").dt.tz_convert("America/Phoenix").dt.tz_localize(None)
    tel["t"] = local.to_numpy(dtype="datetime64[s]")
    tel["lat"], tel["lon"] = tel["lat"].astype(float), tel["lon"].astype(float)
    by_device = {d: g.reset_index(drop=True) for d, g in tel.groupby("device_id")}
    truth = data["truth"][data["truth"]["is_duplicate"] == "false"]
    good = 0
    for row in truth.itertuples():
        g = by_device[fleet[row.truck_id]]
        ts = np.datetime64(pd.Timestamp(row.true_ts_local), "s")
        i = int(np.argmin(np.abs((g["t"].to_numpy() - ts).astype("timedelta64[s]").astype(float))))
        near_in_time = abs((g["t"].iloc[i] - ts) / np.timedelta64(1, "m")) <= MATCH_WINDOW_MIN
        st = stations.loc[row.true_station_id]
        dist = float(haversine_mi(g["lat"].iloc[i], g["lon"].iloc[i], st["lat"], st["lon"]))
        good += int(near_in_time and dist <= MATCH_RADIUS_MI)
    assert good / len(truth) >= MIN_MATCHABLE_SHARE

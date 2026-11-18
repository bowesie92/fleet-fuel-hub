"""Unit tests for the cleaning steps on tiny hand-made frames: timezones, dedupe, odometer, station matching."""

import numpy as np
import pandas as pd
import pytest

from fleetfuel import config
from fleetfuel.normalize import dedupe, odometer, station_match, timezones

CFG = config.load_assumptions()


def ts(text: str) -> pd.Timestamp:
    return pd.Timestamp(text)


# ---- timezones -----------------------------------------------------------------------------------------

def test_utc_pings_become_arizona_local_time():
    utc = pd.Series(pd.to_datetime(["2026-03-02 12:00:00", "2026-07-15 01:30:00"]))
    local = timezones.to_local(utc, "UTC", "America/Phoenix")
    assert local.tolist() == [ts("2026-03-02 05:00:00"), ts("2026-07-14 18:30:00")]  # UTC minus 7 all year, day rolls back
    assert local.dt.tz is None


def test_arizona_source_is_unchanged_and_pacific_source_follows_daylight_saving():
    same = pd.Series(pd.to_datetime(["2026-03-02 05:00:00"]))
    assert timezones.to_local(same, "America/Phoenix", "America/Phoenix").tolist() == same.tolist()
    pacific = pd.Series(pd.to_datetime(["2026-01-15 12:00:00", "2026-07-15 12:00:00"]))
    local = timezones.to_local(pacific, "America/Los_Angeles", "America/Phoenix")
    assert local.tolist() == [ts("2026-01-15 13:00:00"), ts("2026-07-15 12:00:00")]  # Phoenix matches PDT, trails PST by one hour


def test_start_sanity_passes_when_converted_and_flags_when_not():
    plans = pd.DataFrame({"truck_id": ["T1"], "stop_seq": [0], "planned_ts_local": [ts("2026-03-02 05:00:00")]})
    good = pd.DataFrame({"truck_id": ["T1", "T1"], "ts_local": [ts("2026-03-02 05:02:00"), ts("2026-03-02 09:00:00")]})
    ok = timezones.start_time_sanity(good, plans, tolerance_minutes=120)
    assert (ok.compared, ok.flagged) == (1, 0) and ok.median_diff_minutes == pytest.approx(2.0)
    unconverted = pd.DataFrame({"truck_id": ["T1"], "ts_local": [ts("2026-03-02 12:02:00")]})  # still UTC
    bad = timezones.start_time_sanity(unconverted, plans, tolerance_minutes=120)
    assert bad.flagged == 1 and bad.median_diff_minutes == pytest.approx(422.0)


# ---- dedupe --------------------------------------------------------------------------------------------

def swipes(rows):
    return pd.DataFrame(rows, columns=["source_row", "truck_id", "ts_local", "total"]).assign(
        ts_local=lambda d: pd.to_datetime(d["ts_local"])
    )


def test_duplicate_within_window_and_same_amount_is_removed_keeping_the_first():
    df = swipes([(1, "T1", "2026-03-02 06:00", 100.00), (2, "T1", "2026-03-02 06:01", 100.00)])
    kept, removed = dedupe.remove_duplicates(df, window_minutes=2, amount_tolerance_usd=0.01)
    assert kept["source_row"].tolist() == [1]
    assert removed[["source_row", "kept_source_row"]].values.tolist() == [[2, 1]]


def test_different_amount_other_truck_or_outside_window_are_kept():
    df = swipes([
        (1, "T1", "2026-03-02 06:00", 100.00),
        (2, "T1", "2026-03-02 06:01", 100.50),  # different amount: a real second fill
        (3, "T2", "2026-03-02 06:01", 100.00),  # different truck
        (4, "T1", "2026-03-02 06:05", 100.00),  # outside the 2 minute window
    ])
    kept, removed = dedupe.remove_duplicates(df, window_minutes=2, amount_tolerance_usd=0.01)
    assert len(kept) == 4 and removed.empty


def test_amount_tolerance_edge_and_chain_of_three_points_at_the_first_swipe():
    df = swipes([
        (1, "T1", "2026-03-02 06:00", 100.00),
        (2, "T1", "2026-03-02 06:02", 100.01),  # exactly the tolerance and exactly the window
        (3, "T1", "2026-03-02 06:04", 100.00),  # repeats row 2, so also row 1 by chain
    ])
    kept, removed = dedupe.remove_duplicates(df, window_minutes=2, amount_tolerance_usd=0.01)
    assert kept["source_row"].tolist() == [1]
    assert dict(zip(removed["source_row"], removed["kept_source_row"])) == {2: 1, 3: 1}


def test_a_different_fill_between_two_copies_does_not_hide_the_duplicate():
    df = swipes([
        (1, "T1", "2026-03-02 06:00", 100.00),
        (2, "T1", "2026-03-02 06:01", 50.00),
        (3, "T1", "2026-03-02 06:01", 100.00),
    ])
    _kept, removed = dedupe.remove_duplicates(df, window_minutes=2, amount_tolerance_usd=0.01)
    assert removed["source_row"].tolist() == [3]


# ---- odometer ------------------------------------------------------------------------------------------

PINGS = pd.DataFrame({
    "truck_id": ["T1"] * 3,
    "ts_local": pd.to_datetime(["2026-03-02 05:00", "2026-03-02 06:00", "2026-03-02 07:00"]),
    "odometer": [1000.0, 1040.0, 1080.0],
})


def fills(rows):
    return pd.DataFrame(rows, columns=["truck_id", "ts_local", "odometer_raw"]).assign(ts_local=lambda d: pd.to_datetime(d["ts_local"]))


def test_missing_and_implausible_odometers_are_repaired_from_gps_and_flagged():
    df = fills([
        ("T1", "2026-03-02 05:30", np.nan),   # blank: GPS says 1020
        ("T1", "2026-03-02 06:30", 5000.0),   # typo, far from GPS 1060
        ("T1", "2026-03-02 06:30", 1062.0),   # driver value within 25 miles: trusted
    ])
    out, stats = odometer.repair_odometers(df, PINGS, max_gap_mi=25)
    assert out["odometer"].tolist() == [1020.0, 1060.0, 1062.0]
    assert out["odometer_repaired_flag"].tolist() == [True, True, False]
    assert (stats.blank, stats.wrong, stats.repaired, stats.unrepairable) == (1, 1, 2, 0)


def test_no_gps_coverage_means_no_invented_value():
    df = fills([
        ("T1", "2026-03-03 09:00", np.nan),    # after the last ping
        ("T9", "2026-03-02 05:30", np.nan),    # truck with no pings at all
        ("T1", "2026-03-03 09:00", 1234.0),    # card value kept, nothing to compare with
    ])
    out, stats = odometer.repair_odometers(df, PINGS, max_gap_mi=25)
    assert np.isnan(out["odometer"].iloc[0]) and np.isnan(out["odometer"].iloc[1]) and out["odometer"].iloc[2] == 1234.0
    assert not out["odometer_repaired_flag"].any() and stats.unrepairable == 2


# ---- station matching ----------------------------------------------------------------------------------

STATIONS = pd.DataFrame({
    "station_id": ["STN-001", "STN-002", "STN-003"],
    "name": ["Alpha Fuel Stop", "Beta Travel Center", "Beta Travel Express"],
    "brand": ["Alpha", "Beta", "Beta"],
    "lat": [32.0000, 32.1000, 32.1020],
    "lon": [-111.0000, -111.0000, -111.0000],
})


def one_swipe(name, truck_pos=None, city=None, row=1, truck="T1"):
    """A single swipe at 10:00 plus pings around it at truck_pos (lat, lon), or no pings."""
    txns = pd.DataFrame({"source_row": [row], "truck_id": [truck], "ts_local": [ts("2026-03-02 10:00")],
                         "merchant_name": [name], "merchant_city": [city]})
    if truck_pos is None:
        pings = pd.DataFrame({"truck_id": [], "ts_local": pd.to_datetime([]), "lat": [], "lon": [], "odometer": []})
    else:
        pings = pd.DataFrame({"truck_id": [truck] * 2, "ts_local": pd.to_datetime(["2026-03-02 09:55", "2026-03-02 10:10"]),
                              "lat": [truck_pos[0]] * 2, "lon": [truck_pos[1]] * 2, "odometer": [1.0, 1.0]})
    return txns, pings


def test_clear_name_plus_gps_match_has_high_confidence():
    txns, pings = one_swipe("ALPHA FUEL STOP #12 TUCSON", (32.0001, -111.0001), city="Tucson")
    result = station_match.match_stations(txns, STATIONS, pings, CFG)
    row = result.matches.iloc[0]
    assert row["station_id"] == "STN-001" and row["confidence"] > 0.9 and row["method"] == "name+brand+gps"
    assert result.unmatched.empty
    assert result.aliases["STN-001"]["fuel_card_names"] == ["ALPHA FUEL STOP #12 TUCSON"]


def test_a_perfect_name_far_from_the_trucks_gps_is_not_a_candidate():
    txns, pings = one_swipe("Alpha Fuel Stop", (32.5, -111.0))  # 35 miles from the station
    result = station_match.match_stations(txns, STATIONS, pings, CFG)
    assert pd.isna(result.matches.iloc[0]["station_id"])
    reason = result.unmatched.iloc[0]["reason"]
    assert "closest candidate STN-" in reason and "beyond the 1.0 mile limit" in reason and "miles from the truck" in reason


def test_low_confidence_stays_unmatched_with_best_candidate_recorded_never_guessed():
    txns, pings = one_swipe("ZZ QUICK 5", (32.0001, -111.0001))  # right place, name says nothing
    result = station_match.match_stations(txns, STATIONS, pings, CFG)
    match = result.matches.iloc[0]
    assert pd.isna(match["station_id"]) and match["method"] == "unmatched"
    miss = result.unmatched.iloc[0]
    assert miss["best_candidate_station_id"] == "STN-001"
    assert miss["best_confidence"] < config.get(CFG, "pipeline.station_match.min_confidence")
    assert "below the minimum" in miss["reason"]


def test_name_and_brand_alone_can_match_without_gps_at_exactly_the_minimum():
    txns, pings = one_swipe("Alpha Fuel Stop")
    result = station_match.match_stations(txns, STATIONS, pings, CFG)
    match = result.matches.iloc[0]
    assert match["station_id"] == "STN-001" and match["method"] == "name+brand"
    assert match["confidence"] == pytest.approx(config.get(CFG, "pipeline.station_match.min_confidence"))


def test_two_nearly_tied_stations_are_ambiguous_not_guessed():
    txns, pings = one_swipe("BETA", (32.1010, -111.0000))  # brand only, halfway between two Beta stations
    result = station_match.match_stations(txns, STATIONS, pings, CFG)
    assert pd.isna(result.matches.iloc[0]["station_id"])
    assert "ambiguous" in result.unmatched.iloc[0]["reason"]


def test_gps_breaks_a_tie_between_same_brand_stations():
    txns, pings = one_swipe("BETA", (32.1000, -111.0000))  # right at STN-002
    result = station_match.match_stations(txns, STATIONS, pings, CFG)
    assert result.matches.iloc[0]["station_id"] == "STN-002"


def test_a_code_with_no_name_is_tied_to_a_station_only_after_repeated_gps_agreement():
    rows, pings = [], []
    for i in range(3):
        t, p = one_swipe("MERCH 004512", (32.0001, -111.0001), row=i + 1, truck=f"T{i}")
        rows.append(t)
        pings.append(p)
    result = station_match.match_stations(pd.concat(rows), STATIONS, pd.concat(pings), CFG)
    assert result.matches["station_id"].tolist() == ["STN-001"] * 3
    assert set(result.matches["method"]) == {"code+gps"}
    assert result.aliases["STN-001"]["merchant_codes"] == ["MERCH 004512"]
    # the same code seen once is not enough evidence
    result_one = station_match.match_stations(rows[0], STATIONS, pings[0], CFG)
    assert pd.isna(result_one.matches.iloc[0]["station_id"])


def test_clean_name_and_city_stripping():
    assert station_match.clean_name("LOVE'S #812  Tucson") == "loves tucson"
    assert station_match.merchant_text("BRAND #812 TUCSON", "Tucson") == "brand"
    assert station_match.merchant_text("TUCSON", "Tucson") == "tucson"  # never strips down to nothing


def test_distances_are_formatted_with_one_decimal_and_the_right_plural():
    assert station_match.miles_text(1.0) == "1.0 mile"
    assert station_match.miles_text(1.04) == "1.0 mile"
    assert station_match.miles_text(1.4) == "1.4 miles"
    assert station_match.miles_text(27.46) == "27.5 miles"


def test_only_merch_style_codes_are_filed_as_merchant_codes_and_a_stable_label_is_a_name():
    rows, pings = [], []
    for label, base in (("MERCH 004512", 0), ("FUEL STOP #4471", 10)):
        for i in range(3):
            t, p = one_swipe(label, (32.0001, -111.0001), row=base + i + 1, truck=f"T{base + i}")
            rows.append(t)
            pings.append(p)
    result = station_match.match_stations(pd.concat(rows), STATIONS, pd.concat(pings), CFG)
    assert result.matches["station_id"].tolist() == ["STN-001"] * 6  # both labels learned through GPS agreement
    assert result.aliases["STN-001"]["merchant_codes"] == ["MERCH 004512"]
    assert result.aliases["STN-001"]["fuel_card_names"] == ["FUEL STOP #4471"]

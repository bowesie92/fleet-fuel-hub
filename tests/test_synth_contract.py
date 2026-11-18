"""Every synth file has the exact contract columns, types and ordering (docs/contracts.md section 4)."""

from __future__ import annotations

import re

import pandas as pd
import pytest
from test_synth_common import full_cfg, load, run_once

from fleetfuel import config

CONFIG_FLEET = {"linehaul": 6, "local": 14}
LINEHAUL_RUNS = 5


@pytest.fixture(scope="module")
def data():
    return load(run_once("full", full_cfg()))


def all_match(series: pd.Series, pattern: str) -> bool:
    """True when every non-empty cell fully matches the regex."""
    return bool(series.map(lambda v: re.fullmatch(pattern, v) is not None).all())


def test_fleet(data):
    f = data["fleet"]
    assert list(f.columns) == ["truck_id", "home_yard", "role", "tank_gal", "mpg_assumed", "telematics_device_id", "fuel_card_last4"]
    assert (f["role"] == "linehaul").sum() == CONFIG_FLEET["linehaul"]
    assert (f["role"] == "local").sum() == CONFIG_FLEET["local"]
    assert set(f.loc[f["role"] == "linehaul", "home_yard"]) == {"PHX_DC"}
    assert set(f.loc[f["role"] == "local", "home_yard"]) == {"TUC_YARD"}
    assert all_match(f["truck_id"], r"(PHX|TUC)-\d{2}")
    assert all_match(f["telematics_device_id"], r"GPS-\d{5}")
    assert all_match(f["fuel_card_last4"], r"\d{4}")
    assert f["telematics_device_id"].is_unique and f["fuel_card_last4"].is_unique
    assert set(f.loc[f["role"] == "linehaul", "tank_gal"]) == {"200.0"}
    assert set(f.loc[f["role"] == "local", "mpg_assumed"]) == {"6.0"}


def test_partner_program(data):
    p = data["partner_program"]
    assert list(p.columns) == ["station_id", "partner_discount_cpg"]
    assert len(p) > 0 and set(p["partner_discount_cpg"]) == {"12.0"}
    assert list(p["station_id"]) == sorted(p["station_id"])


def test_station_prices(data):
    s = data["station_prices"]
    assert list(s.columns) == ["date", "station_id", "posted_price_per_gal"]
    assert len(s) == 365 * 21  # one row per station per day, unverified stations included
    assert all_match(s["posted_price_per_gal"], r"\d\.\d{3}")
    keys = list(zip(s["date"], s["station_id"]))
    assert keys == sorted(keys)
    assert s["date"].iloc[0] == "2025-11-01" and s["date"].iloc[-1] == "2026-10-31"


def test_fuel_card(data):
    c = data["fuel_card"]
    expected = ["TransactionID", "CardLast4", "Unit", "TransDate", "TransTime", "MerchantName", "MerchantCity",
                "MerchantState", "Product", "Gallons", "PPG", "Total", "Odometer"]
    assert list(c.columns) == expected
    assert all_match(c["TransactionID"], r"FC\d{7}") and c["TransactionID"].is_unique
    assert all_match(c["TransDate"], r"\d{2}/\d{2}/\d{4}") and all_match(c["TransTime"], r"\d{2}:\d{2}")
    assert set(c["MerchantState"]) == {"AZ"} and set(c["Product"]) == {"ULSD"}
    assert all_match(c["Gallons"], r"\d+\.\d{3}") and all_match(c["PPG"], r"\d\.\d{3}")
    assert all_match(c["Total"], r"\d+\.\d{2}")
    gallons, ppg, total = (c[k].astype(float) for k in ("Gallons", "PPG", "Total"))
    assert (gallons * ppg - total).abs().max() <= 0.005 + 1e-6  # Total is Gallons * PPG to the cent
    assert c["Odometer"].map(lambda v: v == "" or v.isdigit()).all()
    stamp = pd.to_datetime(c["TransDate"] + " " + c["TransTime"], format="%m/%d/%Y %H:%M")
    assert stamp.is_monotonic_increasing
    fleet = data["fleet"].set_index("truck_id")["fuel_card_last4"]
    assert (c["Unit"].map(fleet) == c["CardLast4"]).all()


def test_telematics(data):
    t = data["telematics"]
    assert list(t.columns) == ["device_id", "event_time", "lat", "lon", "odometer_mi", "speed_mph"]
    assert all_match(t["event_time"], r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
    assert all_match(t["lat"], r"-?\d+\.\d{5}") and all_match(t["lon"], r"-?\d+\.\d{5}")
    assert all_match(t["odometer_mi"], r"\d+\.\d") and all_match(t["speed_mph"], r"\d+")
    assert set(t["device_id"]) <= set(data["fleet"]["telematics_device_id"])
    assert t["device_id"].is_monotonic_increasing
    for _, g in t.groupby("device_id"):
        assert g["event_time"].is_monotonic_increasing
        assert g["odometer_mi"].astype(float).is_monotonic_increasing


def test_telematics_is_utc(data):
    """Linehaul runs leave Phoenix DC at 02:00 local plus a stagger; Arizona is 7 hours behind UTC, so 09:00, 09:30..."""
    t = data["telematics"]
    linehaul = set(data["fleet"].loc[data["fleet"]["role"] == "linehaul", "telematics_device_id"])
    first = t[t["device_id"].isin(linehaul)].groupby(["device_id", t["event_time"].str[:10]])["event_time"].min()
    fill = int(config.load_assumptions()["fueling"]["fill_minutes"])  # a yard fill starts this long before departure
    departures = [9 * 60 + 30 * k for k in range(LINEHAUL_RUNS)]
    allowed = {f"{m // 60:02d}:{m % 60:02d}:00" for d in departures for m in (d, d - fill)}
    assert set(first.str[11:]) <= allowed and len(set(first.str[11:])) > 1


def test_linehaul_runs_are_staggered_and_rotate(data):
    """Five runs a day, departures staggered, and no tractor runs two linehaul routes on the same day."""
    r = data["route_plans"]
    line = r[r["RouteID"].str.startswith("PHX-L")]
    assert sorted(line["RouteID"].unique()) == [f"PHX-L{i:02d}" for i in range(1, LINEHAUL_RUNS + 1)]
    starts = line[line["StopSeq"] == "0"].drop_duplicates("RouteID").set_index("RouteID")["PlannedArrival"]
    assert starts.is_unique and starts.is_monotonic_increasing
    firsts = line[line["StopSeq"] == "0"]
    assert (firsts.groupby("PlanDate").size() == LINEHAUL_RUNS).all()
    assert not firsts.duplicated(["PlanDate", "Vehicle"]).any()
    assert firsts["Vehicle"].nunique() == CONFIG_FLEET["linehaul"]  # the spare rotates


def test_route_plans(data):
    r = data["route_plans"]
    assert list(r.columns) == ["PlanDate", "RouteID", "Vehicle", "StopSeq", "StopType", "LocationID", "Lat", "Lon",
                               "PlannedArrival", "PlannedMilesFromPrev"]
    assert all_match(r["LocationID"], r"PHX_DC|TUC_YARD|CUST-\d{4}")
    assert all_match(r["PlannedArrival"], r"\d{2}:\d{2}") and all_match(r["Lat"], r"-?\d+\.\d{5}")
    assert set(r["StopType"]) == {"yard", "customer"}
    assert set(r["RouteID"]) == {f"TUC-R{i:02d}" for i in range(1, 11)} | {f"PHX-L{i:02d}" for i in range(1, LINEHAUL_RUNS + 1)}
    keys = list(zip(r["PlanDate"], r["RouteID"], r["StopSeq"].astype(int)))
    assert keys == sorted(keys)
    days = pd.to_datetime(r["PlanDate"])
    assert 6 not in set(days.dt.day_of_week)  # no Sunday routes
    assert (r.loc[r["StopSeq"] == "0", "PlannedMilesFromPrev"] == "0.0").all()


def test_stop_lists_are_fixed_and_sized(data):
    r = data["route_plans"]
    for route_id, g in r.groupby("RouteID"):
        per_day = g.groupby("PlanDate")["LocationID"].apply(lambda s: tuple(s))
        assert per_day.nunique() == 1, route_id
        stops = per_day.iloc[0]
        if route_id.startswith("PHX-L"):
            assert stops == ("PHX_DC", "TUC_YARD", "PHX_DC")
        else:
            customers = stops[1:-1]
            assert stops[0] == stops[-1] == "TUC_YARD"
            assert 8 <= len(customers) <= 12 and len(set(customers)) == len(customers)

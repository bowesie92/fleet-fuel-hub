"""Tests for fleetfuel.stations: manual station overrides and display names (contracts 4.11). Offline, tmp files only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from fleetfuel import stations


def feature(n, ref, name=None, brand=None, operator=None, city=None, tagged=False, truck_brand=False,
            klass="unbranded", access="confirmed", basis="truck_tag"):
    return {
        "type": "Feature", "geometry": {"type": "Point", "coordinates": [-111.0 - n / 100, 32.0 + n / 100]},
        "properties": {
            "station_id": f"STN-{n:03d}", "osm_ref": ref, "name": name, "brand": brand, "operator": operator,
            "city": city, "truck_tagged": tagged, "truck_brand": truck_brand, "station_class": klass,
            "truck_access": access, "access_basis": basis,
        },
    }


@pytest.fixture
def raw_dir(tmp_path):
    feats = [
        feature(1, "node/1", name="Alpha Stop", brand="Alpha", klass="branded", tagged=True),
        feature(2, "node/2", tagged=True),  # unnamed, unbranded
        feature(3, "way/3", name="Alpha Stop West", operator="Alpha Ops", city="Tucson", truck_brand=True,
                klass="travel_center", basis="truck_brand"),
        feature(4, "node/4", name="Scale House", tagged=True),
        feature(5, "node/5", name="Fleet Pumps", klass="unbranded", access="unverified", basis="truck_evidence"),
    ]
    folder = tmp_path / "raw"
    folder.mkdir()
    (folder / "stations.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    return folder


def write_overrides(tmp_path: Path, entries) -> Path:
    path = tmp_path / "overrides.yaml"
    path.write_text(yaml.safe_dump({"overrides": entries}), encoding="utf-8")
    return path


def entry(**kw):
    return {"osm_ref": "node/4", "action": "exclude", "reason": "test reason", **kw}


def test_without_overrides_the_frame_matches_the_geojson(raw_dir, tmp_path):
    frame, records = stations.load_stations(raw_dir, write_overrides(tmp_path, []))
    assert records == []
    assert list(frame["station_id"]) == ["STN-001", "STN-002", "STN-003", "STN-004", "STN-005"]
    assert {"lat", "lon", "truck_access", "access_basis", "station_class"} <= set(frame.columns)
    assert frame.loc[0, "lat"] == 32.01 and frame.loc[0, "lon"] == -111.01


def test_missing_overrides_file_means_no_overrides(raw_dir, tmp_path):
    frame, records = stations.load_stations(raw_dir, tmp_path / "nope.yaml")
    assert len(frame) == 5 and records == []


def test_exclude_drops_the_station(raw_dir, tmp_path):
    frame, records = stations.load_stations(raw_dir, write_overrides(tmp_path, [entry()]))
    assert "node/4" not in set(frame["osm_ref"]) and len(frame) == 4
    assert records == [{"osm_ref": "node/4", "station_id": "STN-004", "action": "exclude",
                        "target_station_id": None, "reason": "test reason", "status": "applied"}]


def test_rename_replaces_the_name(raw_dir, tmp_path):
    e = entry(osm_ref="node/2", action="rename", new_name="Beta Fuel")
    frame, records = stations.load_stations(raw_dir, write_overrides(tmp_path, [e]))
    assert frame.set_index("osm_ref").loc["node/2", "name"] == "Beta Fuel"
    assert records[0]["status"] == "applied" and records[0]["station_id"] == "STN-002"


def test_merge_into_keeps_the_target_id_and_fills_gaps(raw_dir, tmp_path):
    # source way/3 (travel center, has operator and city) merges into node/1 (has name and brand, no operator or city)
    e = entry(osm_ref="way/3", action="merge_into", target_osm_ref="node/1")
    frame, records = stations.load_stations(raw_dir, write_overrides(tmp_path, [e]))
    assert "way/3" not in set(frame["osm_ref"])
    row = frame.set_index("osm_ref").loc["node/1"]
    assert row["station_id"] == "STN-001"
    assert row["name"] == "Alpha Stop" and row["brand"] == "Alpha"  # the target's own values are kept
    assert row["operator"] == "Alpha Ops" and row["city"] == "Tucson"  # empty fields filled from the source
    assert bool(row["truck_tagged"]) and bool(row["truck_brand"])  # flags ORed
    assert row["station_class"] == "travel_center"  # recomputed
    assert row["access_basis"] == "truck_tag"  # the stronger basis
    assert list(row["merged_osm_refs"]) == ["way/3"]
    assert records[0] == {"osm_ref": "way/3", "station_id": "STN-003", "action": "merge_into",
                          "target_station_id": "STN-001", "reason": "test reason", "status": "applied"}


def test_merge_keeps_the_stronger_truck_access(raw_dir, tmp_path):
    e = entry(osm_ref="node/1", action="merge_into", target_osm_ref="node/5")
    frame, _ = stations.load_stations(raw_dir, write_overrides(tmp_path, [e]))
    row = frame.set_index("osm_ref").loc["node/5"]
    assert row["truck_access"] == "confirmed" and row["access_basis"] == "truck_tag"
    assert row["name"] == "Fleet Pumps" and row["brand"] == "Alpha"


def test_unknown_osm_ref_is_reported_not_an_error(raw_dir, tmp_path):
    e = [entry(osm_ref="node/999", station_id="STN-099"),
         entry(osm_ref="node/1", action="merge_into", target_osm_ref="node/998")]
    frame, records = stations.load_stations(raw_dir, write_overrides(tmp_path, e))
    assert len(frame) == 5
    assert [r["status"] for r in records] == ["not_found", "not_found"]
    assert records[0]["station_id"] == "STN-099"


def test_every_entry_gets_one_record_in_file_order(raw_dir, tmp_path):
    e = [entry(), entry(osm_ref="node/2", action="rename", new_name="Beta"), entry(osm_ref="node/404")]
    _, records = stations.load_stations(raw_dir, write_overrides(tmp_path, e))
    assert [(r["osm_ref"], r["status"]) for r in records] == [("node/4", "applied"), ("node/2", "applied"), ("node/404", "not_found")]


def test_missing_geojson_gives_a_plain_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="fleetfuel fetch"):
        stations.load_stations(tmp_path, tmp_path / "x.yaml")


def test_real_overrides_file_validates():
    assert isinstance(stations.load_overrides(), list)


@pytest.mark.parametrize("bad, message", [
    (entry(osm_ref="123"), "osm_ref"),
    (entry(action="delete"), "action"),
    (entry(reason=""), "reason"),
    (entry(action="merge_into"), "target_osm_ref"),
    (entry(action="merge_into", target_osm_ref="oops"), "target_osm_ref"),
    (entry(action="merge_into", target_osm_ref="node/4"), "itself"),
    (entry(action="rename"), "new_name"),
    (entry(colour="red"), "unknown fields"),
    ("not a mapping", "mapping"),
])
def test_bad_entries_are_rejected(tmp_path, bad, message):
    with pytest.raises(ValueError, match=message):
        stations.load_overrides(write_overrides(tmp_path, [bad]))


def test_duplicate_osm_ref_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="more than once"):
        stations.load_overrides(write_overrides(tmp_path, [entry(), entry()]))


def test_overrides_file_must_hold_a_list(tmp_path):
    path = tmp_path / "o.yaml"
    path.write_text("overrides: 5\n", encoding="utf-8")
    with pytest.raises(ValueError, match="list"):
        stations.load_overrides(path)


def test_display_name_prefers_name_then_brand_then_a_placeholder(raw_dir, tmp_path):
    frame, _ = stations.load_stations(raw_dir, write_overrides(tmp_path, []))
    rows = frame.set_index("osm_ref")
    assert stations.display_name(rows.loc["node/1"].to_dict() | {"station_id": "STN-001"}) == "Alpha Stop"
    assert stations.display_name({"name": None, "brand": "Alpha", "station_id": "STN-009"}) == "Alpha"
    assert stations.display_name(frame.iloc[1]) == "Unnamed fuel station · STN-002"
    assert stations.display_name({"name": float("nan"), "brand": "", "station_id": "STN-010"}) == "Unnamed fuel station · STN-010"


# ---------------------------------------------------------------- add action (contract 4.11)

def add_entry(**kw):
    base = {"action": "add", "add_id": "A01", "name": "Delta Truck Stop", "brand": "Delta", "city": "Tucson",
            "lat": 32.5, "lon": -110.5, "source_url": "https://example.com/locator/1", "reason": "chain locator lists diesel lanes"}
    return {**base, **kw}


def test_add_far_from_everything_creates_a_new_confirmed_station(raw_dir, tmp_path):
    frame, records = stations.load_stations(raw_dir, write_overrides(tmp_path, [add_entry()]))
    assert len(frame) == 6
    row = frame.set_index("station_id").loc["STN-A01"]
    assert row["osm_ref"] is None and row["name"] == "Delta Truck Stop" and row["city"] == "Tucson"
    assert (row["truck_access"], row["access_basis"], row["station_class"]) == ("confirmed", "chain_locator", "branded")
    assert row["source_url"] == "https://example.com/locator/1" and row["add_id"] == "A01"
    assert list(frame["station_id"])[-1] == "STN-A01"
    assert records[0]["status"] == "applied_new" and records[0]["target_station_id"] == "STN-A01"
    assert stations.display_name(row) == "Delta Truck Stop"


def test_add_next_to_an_existing_station_confirms_it_and_fills_gaps(raw_dir, tmp_path):
    # node/5 sits at 32.05, -111.05 and is unverified with no brand or city
    e = add_entry(lat=32.0501, lon=-111.0501, brand="Echo", name="Echo Fleet")
    frame, records = stations.load_stations(raw_dir, write_overrides(tmp_path, [e]))
    assert len(frame) == 5
    row = frame.set_index("osm_ref").loc["node/5"]
    assert row["station_id"] == "STN-005"
    assert (row["truck_access"], row["access_basis"]) == ("confirmed", "chain_locator")
    assert row["name"] == "Fleet Pumps" and row["brand"] == "Echo" and row["city"] == "Tucson"
    assert row["add_id"] == "A01" and row["source_url"].startswith("https://")
    assert records[0]["status"] == "applied_matched" and records[0]["target_station_id"] == "STN-005"


@pytest.mark.parametrize("missing", ["add_id", "name", "lat", "lon", "source_url", "reason"])
def test_add_needs_its_required_fields(tmp_path, missing):
    bad = add_entry()
    bad.pop(missing)
    with pytest.raises(ValueError, match=missing):
        stations.load_overrides(write_overrides(tmp_path, [bad]))


def test_add_brand_and_city_are_optional(tmp_path):
    bad = add_entry()
    bad.pop("brand")
    bad.pop("city")
    checked = stations.load_overrides(write_overrides(tmp_path, [bad]))
    assert checked[0]["brand"] is None and checked[0]["city"] is None


def test_two_adds_on_one_station_both_land_there(raw_dir, tmp_path):
    adds = [add_entry(), add_entry(add_id="A02", lat=32.5002, lon=-110.5002, source_url="https://example.com/locator/2")]
    frame, records = stations.load_stations(raw_dir, write_overrides(tmp_path, adds))
    assert len(frame) == 6 and (frame["station_id"] == "STN-A01").sum() == 1
    assert [r["status"] for r in records] == ["applied_new", "applied_matched"]
    assert records[1]["target_station_id"] == "STN-A01"
    assert frame.set_index("station_id").loc["STN-A01", "source_url"].endswith("/1")  # the first add keeps the source


def test_duplicate_add_id_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="add_id"):
        stations.load_overrides(write_overrides(tmp_path, [add_entry(), add_entry(lat=32.9)]))


def test_add_runs_after_the_other_actions(raw_dir, tmp_path):
    # node/4 is excluded, so an add at its location creates a new station instead of matching it
    e = [add_entry(lat=32.04, lon=-111.04), entry()]
    frame, records = stations.load_stations(raw_dir, write_overrides(tmp_path, e))
    assert "STN-A01" in set(frame["station_id"]) and "node/4" not in set(frame["osm_ref"])
    assert [r["status"] for r in records] == ["applied_new", "applied"]


def test_add_truck_brand_makes_a_travel_center(raw_dir, tmp_path):
    frame, _ = stations.load_stations(raw_dir, write_overrides(tmp_path, [add_entry(brand="Pilot", name="Pilot Travel Center")]))
    assert frame.set_index("station_id").loc["STN-A01", "station_class"] == "travel_center"


def test_add_cardlock_brand_or_name_is_classed_cardlock(raw_dir, tmp_path):
    e = [add_entry(brand="CFN", name="CFN Cardlock"), add_entry(add_id="A02", lat=32.9, lon=-110.9, brand=None, name="Pacific Pride Fleet")]
    frame, _ = stations.load_stations(raw_dir, write_overrides(tmp_path, e))
    classes = frame.set_index("station_id")["station_class"]
    assert classes["STN-A01"] == "cardlock" and classes["STN-A02"] == "cardlock"


def test_matched_add_recomputes_the_class(raw_dir, tmp_path):
    e = add_entry(lat=32.0501, lon=-111.0501, brand="CFN", name="CFN Cardlock")
    frame, _ = stations.load_stations(raw_dir, write_overrides(tmp_path, [e]))
    assert frame.set_index("osm_ref").loc["node/5", "station_class"] == "cardlock"

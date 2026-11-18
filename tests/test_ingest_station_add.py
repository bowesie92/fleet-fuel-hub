"""The `add` station override (contract 4.11): logging, dim_station contents and matching at added stations."""

import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from fleetfuel import config, db, ingest, normalize
from fleetfuel import stations as station_module
from fleetfuel.normalize import station_match

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pipeline"
URL = "https://example.test/locator/"
ADDS = f'''
  - add_id: "A01"
    action: add
    name: "Saguaro Fuel and Go"
    brand: "Saguaro Fuel"
    city: "Tucson"
    lat: 32.2226
    lon: -110.9747
    source_url: "{URL}one"
    reason: "Locator lists diesel lanes at an existing station."
  - add_id: "A02"
    action: add
    name: "Fixture Cardlock 5915 Tucson"
    brand: "Fixture Cardlock"
    city: "Tucson"
    lat: 32.3
    lon: -110.99
    source_url: "{URL}two"
    reason: "Locator lists 18 wheel access."
  - add_id: "A09"
    action: add
    name: "Second Site"
    lat: 32.31
    lon: -110.98
    source_url: "{URL}three"
    reason: "Another new site."
'''


@pytest.fixture
def overrides(tmp_path):
    path = tmp_path / "overrides.yaml"
    path.write_text((FIXTURES / "station_overrides.yaml").read_text(encoding="utf-8") + ADDS, encoding="utf-8")
    return path


@pytest.fixture
def hub(tmp_path, overrides):
    raw = tmp_path / "raw"
    shutil.copytree(FIXTURES, raw, ignore=shutil.ignore_patterns("ground_truth", "make_fixtures.py", "__pycache__"))
    cfg = config.load_assumptions()
    ingest.run(cfg, raw_dir=raw, db_path=tmp_path / "h.duckdb", overrides_path=overrides)
    log = db.connect(tmp_path / "h.duckdb")
    notes = log.execute("SELECT notes, rows_out, issue_count FROM dq_log WHERE check_name = 'station_override'").fetchall()
    log.close()
    normalize.run(cfg, raw_dir=raw, db_path=tmp_path / "h.duckdb")
    con = db.connect(tmp_path / "h.duckdb")
    yield con, notes
    con.close()


def test_add_records_have_both_statuses_and_count_as_applied(hub):
    _con, notes = hub
    by_text = {n: (out, issues) for n, out, issues in notes}
    matched = next(n for n in by_text if n.startswith("Confirmed STN-002"))
    new = next(n for n in by_text if n.startswith("Added STN-A02"))
    assert "Status: applied_matched." in matched and f"Source: {URL}one." in matched
    assert "(Fixture Cardlock 5915 Tucson)" in new and "Status: applied_new." in new and "Locator lists 18 wheel access." in new
    assert by_text[matched] == (1, 0) and by_text[new] == (1, 0)  # applied adds are not issues
    assert any("Added STN-A09 (Second Site)" in n for n in by_text)
    not_found = next(n for n in by_text if "STN-099" in n)
    assert by_text[not_found] == (0, 1) and "not applied" in not_found


def test_dim_station_carries_add_fields(hub):
    con, _ = hub
    df = con.execute("SELECT * FROM dim_station").df().set_index("station_id")
    new = df.loc["STN-A02"]
    ids = json.loads(new["source_ids_json"])
    assert ids["osm_ref"] is None and ids["add_id"] == "A02" and ids["source_url"] == f"{URL}two" and ids["merged_osm_refs"] == []
    assert (new["truck_access"], new["access_basis"], new["station_class"]) == ("confirmed", "chain_locator", "cardlock")
    confirmed = df.loc["STN-002"]
    assert (confirmed["truck_access"], confirmed["access_basis"]) == ("confirmed", "chain_locator")
    assert json.loads(confirmed["source_ids_json"])["add_id"] == "A01" and json.loads(confirmed["source_ids_json"])["osm_ref"] == "node/1002"
    assert "add_id" not in json.loads(df.loc["STN-001"]["source_ids_json"])


def test_swipes_at_added_stations_are_matched(overrides):
    cfg = config.load_assumptions()
    stations, _ = station_module.load_stations(FIXTURES, overrides)
    spot = (32.3, -110.99)
    names = ["Fixture Cardlock 5915 Tucson", "FIXTURE CARDLOCK #5915 TUCSON", "FIXTURE CARDLOCK"]
    txns = pd.DataFrame({"source_row": [1, 2, 3], "truck_id": ["T1", "T2", "T3"], "ts_local": [pd.Timestamp("2026-03-02 10:00")] * 3,
                         "merchant_name": names, "merchant_city": ["Tucson"] * 3})
    pings = pd.DataFrame({"truck_id": ["T1", "T2", "T3"] * 2, "ts_local": pd.to_datetime(["2026-03-02 09:58"] * 3 + ["2026-03-02 10:08"] * 3),
                          "lat": [spot[0]] * 6, "lon": [spot[1]] * 6, "odometer": [1.0] * 6})
    result = station_match.match_stations(txns, stations, pings, cfg)
    assert result.matches["station_id"].tolist() == ["STN-A02"] * 3

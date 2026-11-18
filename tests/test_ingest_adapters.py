"""Ingest adapters: required columns, parsed types and honest counting of rows that fail."""

import shutil
from pathlib import Path

import pandas as pd
import pytest

from fleetfuel import config, db, ingest
from fleetfuel.ingest import fuel_card, reference, route_plans, telematics
from fleetfuel.ingest.validate import SourceError

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pipeline"


@pytest.fixture
def raw(tmp_path):
    """A writable copy of the hand-made fixtures (without ground truth, which ingest never reads)."""
    dest = tmp_path / "raw"
    shutil.copytree(FIXTURES, dest, ignore=shutil.ignore_patterns("ground_truth", "make_fixtures.py", "__pycache__"))
    return dest


def test_fuel_card_fixture_parses_every_row_with_types(raw):
    result = fuel_card.load(raw / "fuel_card.csv")
    assert (result.rows_in, result.rows_out, result.rejected) == (18, 18, 0)
    df = result.df
    assert pd.api.types.is_datetime64_any_dtype(df["ts_source"])
    assert df["source_row"].tolist() == list(range(1, 19))
    assert df["odometer_raw"].isna().sum() == 3  # blank odometers stay blank for the repair step
    assert result.issue_count == 3 and "blank or unreadable odometer" in result.note()


def test_fuel_card_counts_unparseable_rows_instead_of_dropping_silently(raw):
    path = raw / "fuel_card.csv"
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.loc[0, "Gallons"] = "abc"
    df.loc[1, "TransDate"] = "13/45/2026"
    df.loc[2, "MerchantName"] = ""
    df.loc[3, "TransactionID"] = df.loc[4, "TransactionID"]  # repeated id
    df.to_csv(path, index=False, lineterminator="\n")
    result = fuel_card.load(path)
    assert result.rejected == 4 and result.rows_out == 14
    assert set(result.reasons) == {"bad gallons", "bad date or time", "blank merchant name", "repeated transaction id"}
    assert "4 could not be used" in result.note()
    assert 1 not in set(result.df["source_row"])  # source_row keeps pointing at the original file row


def test_fuel_card_recovers_a_blank_unit_from_the_card_digits(raw):
    path = raw / "fuel_card.csv"
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.loc[0, "Unit"] = ""
    df.to_csv(path, index=False, lineterminator="\n")
    fleet = reference.load_fleet(raw / "fleet.csv").df
    result = fuel_card.load(
        path, card_to_truck=dict(zip(fleet["fuel_card_last4"], fleet["truck_id"])), known_trucks=set(fleet["truck_id"])
    )
    assert result.rejected == 0
    assert result.df.loc[0, "truck_id"] == "PHX-01"


def test_missing_required_column_raises_a_clear_error(raw):
    path = raw / "fuel_card.csv"
    df = pd.read_csv(path, dtype=str, keep_default_na=False).drop(columns=["PPG"])
    df.to_csv(path, index=False, lineterminator="\n")
    with pytest.raises(SourceError, match="missing required columns: PPG"):
        fuel_card.load(path)


def test_missing_file_raises_a_plain_english_error(tmp_path):
    with pytest.raises(SourceError, match="not found"):
        telematics.load(tmp_path / "telematics.csv")


def test_telematics_maps_devices_and_sets_aside_unknown_ones(raw):
    path = raw / "telematics.csv"
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.loc[0, "device_id"] = "GPS-99999"
    df.loc[1, "lat"] = "not a number"
    df = pd.concat([df, df.iloc[[5]]])  # one exact repeated ping
    df.to_csv(path, index=False, lineterminator="\n")
    fleet = reference.load_fleet(raw / "fleet.csv").df
    result = telematics.load(path, dict(zip(fleet["telematics_device_id"], fleet["truck_id"])))
    assert result.rejected == 2 and set(result.reasons) == {"unknown device", "bad coordinates"}
    assert result.extra_issues == 1 and result.rows_out == 244 - 2
    assert set(result.df["truck_id"]) == {"PHX-01", "TUC-01", "TUC-02"}


def test_route_plans_parse_and_reject_bad_rows(raw):
    path = raw / "route_plans.csv"
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.loc[0, "PlannedArrival"] = "25:99"
    df.to_csv(path, index=False, lineterminator="\n")
    result = route_plans.load(path)
    assert result.rows_in == 75 and result.rejected == 1 and result.reasons == {"bad planned time": 1}
    assert result.df["stop_seq"].dtype.kind == "i"


def test_reference_loaders_on_fixtures(raw):
    assert reference.load_fleet(raw / "fleet.csv").rows_out == 3
    assert reference.load_partners(raw / "partner_program.csv").rows_out == 1
    assert reference.load_station_prices(raw / "station_prices.csv").rows_out == 25
    assert reference.load_eia(raw / "eia_diesel.csv").rows_out == 2
    stations, records = reference.load_stations(raw, raw / "station_overrides.yaml")
    assert stations.rows_out == 4  # STN-005 was merged into STN-004 by the fixture overrides
    row = stations.df.set_index("station_id").loc["STN-001"]
    assert (row["lat"], row["lon"]) == (32.405, -111.21) and row["truck_brand"] and row["truck_access"] == "confirmed"
    assert [(r["action"], r["status"]) for r in records] == [("merge_into", "applied"), ("exclude", "not_found")]
    assert stations.df.set_index("station_id").loc["STN-004", "merged_osm_refs"] == '["node/1005"]'


def test_missing_station_file_raises_a_plain_english_error(raw):
    (raw / "stations.geojson").unlink()
    with pytest.raises(SourceError, match="station list"):
        reference.load_stations(raw, raw / "station_overrides.yaml")


def test_eia_repeated_week_is_collapsed_and_counted(raw):
    path = raw / "eia_diesel.csv"
    path.write_text(path.read_text(encoding="utf-8") + "2026-03-02,3.9,X\n", encoding="utf-8")
    result = reference.load_eia(path)
    assert result.rows_out == 2 and result.reasons == {"repeated week": 1}


def test_ingest_run_stages_sources_and_logs_one_row_per_source(raw, tmp_path):
    db_path = tmp_path / "hub.duckdb"
    ingest.run(config.load_assumptions(), raw_dir=raw, db_path=db_path, overrides_path=raw / "station_overrides.yaml")
    con = db.connect(db_path)
    try:
        checks = [r[0] for r in con.execute("SELECT check_name FROM dq_log").fetchall()]
        assert sorted(checks) == sorted(
            ["ingest_fleet", "ingest_stations", "ingest_partner_program", "ingest_station_prices", "ingest_eia",
             "ingest_fuel_card", "ingest_telematics", "ingest_route_plans", "ingest_yard_fuel", "station_override", "station_override"]
        )
        assert con.execute("SELECT count(*) FROM stg_fuel_card").fetchone()[0] == 18
        assert con.execute("SELECT count(*) FROM dim_truck").fetchone()[0] == 3
        assert con.execute("SELECT count(*) FROM ref_station_price").fetchone()[0] == 20  # the merged-away station's 5 price rows are dropped
        assert con.execute("SELECT count(*) FROM ref_eia_weekly").fetchone()[0] == 2
    finally:
        con.close()


def test_ingest_run_fails_clearly_when_an_input_is_missing(raw, tmp_path):
    (raw / "fuel_card.csv").unlink()
    with pytest.raises(SourceError, match="fuel card export"):
        ingest.run(config.load_assumptions(), raw_dir=raw, db_path=tmp_path / "hub.duckdb", overrides_path=raw / "station_overrides.yaml")

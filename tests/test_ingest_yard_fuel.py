"""Yard fuel (Phoenix DC tank log): loads, types, dq_log notes, missing file, and no leakage into card facts."""

import shutil
from pathlib import Path

import pandas as pd
import pytest

from fleetfuel import config, db, ingest, normalize
from fleetfuel.ingest import yard_fuel
from fleetfuel.ingest.validate import SourceError

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pipeline"
YARD_FILLS = 3
CARD_SWIPES = 18
CARD_AFTER_DEDUPE = 16


@pytest.fixture
def raw(tmp_path):
    """A writable copy of the hand-made fixtures."""
    dest = tmp_path / "raw"
    shutil.copytree(FIXTURES, dest, ignore=shutil.ignore_patterns("ground_truth", "make_fixtures.py", "__pycache__"))
    return dest


def _ingest(raw, tmp_path):
    db_path = tmp_path / "hub.duckdb"
    ingest.run(config.load_assumptions(), raw_dir=raw, db_path=db_path, overrides_path=FIXTURES / "station_overrides.yaml")
    return db_path


def test_adapter_parses_types_and_local_time(raw):
    result = yard_fuel.load(raw / "yard_fuel.csv")
    assert (result.rows_in, result.rows_out, result.rejected) == (YARD_FILLS, YARD_FILLS, 0)
    assert pd.api.types.is_datetime64_any_dtype(result.df["ts_local"])
    assert result.df.loc[0, "ts_local"] == pd.Timestamp("2026-03-02 04:40")
    assert result.df["gallons"].dtype == "float64"


def test_adapter_counts_bad_rows_and_checks_columns(raw):
    path = raw / "yard_fuel.csv"
    df = pd.read_csv(path, dtype=str)
    df.loc[0, "Gallons"] = "abc"
    df.loc[1, "FillTime"] = "99:99"
    df.to_csv(path, index=False, lineterminator="\n")
    result = yard_fuel.load(path)
    assert result.rejected == 2 and result.rows_out == 1
    assert "2 rows could not be used" in yard_fuel.note(result)
    df.drop(columns=["Total"]).to_csv(path, index=False, lineterminator="\n")
    with pytest.raises(SourceError, match="Total"):
        yard_fuel.load(path)


def test_ingest_loads_fact_yard_fuel_with_plain_dq_note(raw, tmp_path):
    con = db.connect(_ingest(raw, tmp_path))
    try:
        assert con.execute("SELECT count(*) FROM fact_yard_fuel").fetchone()[0] == YARD_FILLS
        types = dict(
            con.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'fact_yard_fuel'").fetchall()
        )
        assert types["ts_local"] == "TIMESTAMP" and types["gallons"] == "DOUBLE"
        note = con.execute("SELECT notes FROM dq_log WHERE check_name = 'ingest_yard_fuel'").fetchone()[0]
        assert note.startswith("Read 3 on-site fills from the Phoenix DC tank log (yard_fuel.csv); these are not fuel card transactions")
    finally:
        con.close()


def test_missing_file_with_a_tank_configured_raises(raw, tmp_path):
    (raw / "yard_fuel.csv").unlink()
    with pytest.raises(SourceError, match=r"yard_fuel\.csv.*Phoenix DC"):
        _ingest(raw, tmp_path)


def test_missing_tank_error_uses_the_configured_yard_label(raw, tmp_path):
    (raw / "yard_fuel.csv").unlink()
    cfg = config.load_assumptions()
    cfg["yards"]["phoenix_dc"]["label"] = "North depot"
    with pytest.raises(SourceError, match="North depot"):
        ingest.run(cfg, raw_dir=raw, db_path=tmp_path / "hub.duckdb", overrides_path=FIXTURES / "station_overrides.yaml")


def test_missing_yard_config_keys_raise_instead_of_meaning_no_tank(raw, tmp_path):
    (raw / "yard_fuel.csv").unlink()
    cfg = config.load_assumptions()
    del cfg["yards"]["phoenix_dc"]["onsite_fuel"]
    with pytest.raises(KeyError, match="onsite_fuel"):
        ingest.run(cfg, raw_dir=raw, db_path=tmp_path / "a.duckdb", overrides_path=FIXTURES / "station_overrides.yaml")
    del cfg["yards"]
    with pytest.raises(KeyError, match="yards"):
        ingest.run(cfg, raw_dir=raw, db_path=tmp_path / "b.duckdb", overrides_path=FIXTURES / "station_overrides.yaml")


def test_missing_file_with_no_tank_loads_zero_rows_and_says_so(raw, tmp_path):
    (raw / "yard_fuel.csv").unlink()
    cfg = config.load_assumptions()
    for yard in cfg["yards"].values():
        yard["onsite_fuel"] = False
    db_path = tmp_path / "hub.duckdb"
    ingest.run(cfg, raw_dir=raw, db_path=db_path, overrides_path=FIXTURES / "station_overrides.yaml")
    con = db.connect(db_path)
    try:
        assert con.execute("SELECT count(*) FROM fact_yard_fuel").fetchone()[0] == 0
        rows_in, note = con.execute("SELECT rows_in, notes FROM dq_log WHERE check_name = 'ingest_yard_fuel'").fetchone()
        assert rows_in == 0 and "zero on-site fills" in note
    finally:
        con.close()


def test_yard_fills_never_reach_card_tables(raw, tmp_path):
    db_path = _ingest(raw, tmp_path)
    normalize.run(config.load_assumptions(), raw_dir=raw, db_path=db_path)
    con = db.connect(db_path)
    try:
        assert con.execute("SELECT count(*) FROM fact_yard_fuel").fetchone()[0] == YARD_FILLS
        assert con.execute("SELECT count(*) FROM fact_fuel_txn").fetchone()[0] == CARD_AFTER_DEDUPE
        assert con.execute("SELECT max(source_row) FROM fact_fuel_txn").fetchone()[0] <= CARD_SWIPES
        assert con.execute("SELECT count(*) FROM dq_duplicate").fetchone()[0] == 2
    finally:
        con.close()

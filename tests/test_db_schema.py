"""The hub schema matches docs/contracts.md section 6 exactly, and the helpers behave."""

import re
from pathlib import Path

import pytest

from fleetfuel import db

CONTRACTS = Path(__file__).resolve().parents[1] / "docs" / "contracts.md"


def _contract_statements() -> list[str]:
    """Pull the CREATE TABLE statements out of the SQL block in contract section 6."""
    text = CONTRACTS.read_text(encoding="utf-8")
    block = re.search(r"## 6\. DuckDB schema.*?```sql\n(.*?)```", text, re.S).group(1)
    uncommented = re.sub(r"--[^\n]*", "", block)  # comments in the contract contain semicolons
    return [_normalize(s) for s in uncommented.split(";") if s.strip()]


def _normalize(statement: str) -> str:
    """Drop comments and collapse whitespace so only names and types are compared."""
    no_comments = re.sub(r"--[^\n]*", "", statement)
    return re.sub(r"\s+", " ", no_comments).strip()


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "hub.duckdb")
    yield connection
    connection.close()


def test_ddl_matches_the_contract_character_for_character():
    ours = [_normalize(s) for s in db.SCHEMA_DDL]
    assert ours == _contract_statements()


def test_create_schema_builds_every_contract_table(con):
    db.create_schema(con)
    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    assert tables == set(db.CONTRACT_TABLES)


def test_create_schema_is_idempotent_and_clears_old_tables(con):
    db.create_schema(con)
    db.log_dq(con, "x", 1, 1, 0, "note")
    con.execute("CREATE TABLE stg_leftover (a INTEGER)")
    db.create_schema(con)
    assert con.execute("SELECT count(*) FROM dq_log").fetchone()[0] == 0
    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    assert "stg_leftover" not in tables


def test_log_dq_writes_one_row(con):
    db.create_schema(con)
    db.log_dq(con, "dedupe_swipes", 10, 8, 2, "Removed 2.")
    assert con.execute("SELECT * FROM dq_log").fetchall() == [("dedupe_swipes", 10, 8, 2, "Removed 2.")]


def test_column_types_match_the_contract(con):
    db.create_schema(con)
    cols = dict(con.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'fact_fuel_txn'").fetchall())
    assert cols["ts_local"] == "TIMESTAMP"
    assert cols["odometer_repaired_flag"] == "BOOLEAN"
    assert cols["source_row"] == "INTEGER"
    assert cols["station_match_confidence"] == "DOUBLE"
    assert con.execute("SELECT data_type FROM information_schema.columns WHERE table_name = 'fact_route_day' AND column_name = 'date'").fetchone()[0] == "DATE"


def test_primary_key_blocks_a_repeated_txn_id(con):
    db.create_schema(con)
    insert = "INSERT INTO fact_fuel_txn VALUES ('FC1','T',NULL,'2026-03-02 05:00:00',1,1,1,NULL,false,1,NULL,NULL)"
    con.execute(insert)
    with pytest.raises(Exception):
        con.execute(insert)

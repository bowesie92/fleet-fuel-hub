"""The DuckDB hub: the one database every later step reads (docs/contracts.md section 6).

In business terms this file is the "single app": one file with clean dimension tables
(stations, trucks, routes), fact tables (fuel fills, GPS pings, route days), reference
tables (EIA prices, posted prices, detours) and data-quality tables that show how much
cleanup was done. All timestamps in it are naive America/Phoenix local time.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

# The DDL below is copied from docs/contracts.md section 6. Names and types must match it exactly.
SCHEMA_DDL: list[str] = [
    """
    CREATE TABLE dim_station (
        station_id           VARCHAR PRIMARY KEY,   -- STN-###
        name                 VARCHAR,
        brand                VARCHAR,
        lat                  DOUBLE NOT NULL,
        lon                  DOUBLE NOT NULL,
        station_class        VARCHAR NOT NULL,      -- added: travel_center | branded | unbranded
        is_partner           BOOLEAN NOT NULL,
        partner_discount_cpg DOUBLE NOT NULL,       -- cents per gallon, 0 for non-partners
        source_ids_json      VARCHAR NOT NULL,      -- JSON: {"osm_ref": "...", "merged_osm_refs": [...], "fuel_card_names": [...], "merchant_codes": [...]}
        truck_access         VARCHAR NOT NULL,      -- added: confirmed | unverified
        access_basis         VARCHAR NOT NULL       -- added: truck_tag | truck_brand | cardlock | fleet_access | truck_evidence
    )
    """,
    """
    CREATE TABLE dim_truck (
        truck_id     VARCHAR PRIMARY KEY,
        home_yard    VARCHAR NOT NULL,              -- PHX_DC | TUC_YARD
        role         VARCHAR NOT NULL,              -- linehaul | local
        tank_gal     DOUBLE NOT NULL,
        mpg_assumed  DOUBLE NOT NULL
    )
    """,
    """
    CREATE TABLE dim_route (
        route_id          VARCHAR PRIMARY KEY,      -- TUC-R01 | PHX-L01
        yard              VARCHAR NOT NULL,
        stops_json        VARCHAR NOT NULL,         -- JSON list: [{"seq": 0, "location_id": "TUC_YARD", "label": "Tucson yard", "lat": .., "lon": ..}, ...]
        base_miles        DOUBLE,                   -- Wave 1: sum of planned miles; Wave 2: road miles
        base_minutes      DOUBLE,                   -- Wave 1: planned drive plus stop minutes; Wave 2: road minutes plus stop minutes
        geometry_geojson  VARCHAR                   -- Wave 1: straight-line LineString through the stops; Wave 2: road geometry
    )
    """,
    """
    CREATE TABLE fact_fuel_txn (
        txn_id                    VARCHAR PRIMARY KEY,  -- fuel card TransactionID of the kept row
        truck_id                  VARCHAR NOT NULL,
        station_id                VARCHAR,              -- NULL when unmatched (never guessed)
        ts_local                  TIMESTAMP NOT NULL,
        gallons                   DOUBLE NOT NULL,
        price_per_gal             DOUBLE NOT NULL,      -- price paid, net of partner discount
        total                     DOUBLE NOT NULL,
        odometer                  DOUBLE,               -- after repair
        odometer_repaired_flag    BOOLEAN NOT NULL,
        source_row                INTEGER NOT NULL,     -- 1-based row in fuel_card.csv
        station_match_confidence  DOUBLE,               -- added: 0 to 1
        station_match_method      VARCHAR               -- added: e.g. name+brand+gps | gps_only | unmatched
    )
    """,
    """
    CREATE TABLE fact_yard_fuel (                   -- added (v1.6): on-site yard fills from yard_fuel.csv, not card transactions
        truck_id       VARCHAR NOT NULL,
        yard           VARCHAR NOT NULL,            -- PHX_DC
        ts_local       TIMESTAMP NOT NULL,
        gallons        DOUBLE NOT NULL,
        price_per_gal  DOUBLE NOT NULL,
        total          DOUBLE NOT NULL
    )
    """,
    """
    CREATE TABLE fact_ping (
        truck_id  VARCHAR NOT NULL,
        ts_local  TIMESTAMP NOT NULL,
        lat       DOUBLE NOT NULL,
        lon       DOUBLE NOT NULL,
        odometer  DOUBLE
    )
    """,
    """
    CREATE TABLE fact_route_day (
        route_id      VARCHAR NOT NULL,
        truck_id      VARCHAR NOT NULL,
        date          DATE NOT NULL,
        actual_miles  DOUBLE                         -- from the telematics odometer for that truck-day
    )
    """,
    """
    CREATE TABLE ref_eia_weekly (
        week_start     DATE PRIMARY KEY,
        price_per_gal  DOUBLE NOT NULL
    )
    """,
    """
    CREATE TABLE ref_station_price (                 -- added: posted price feed (4.6)
        station_id            VARCHAR NOT NULL,
        date                  DATE NOT NULL,
        posted_price_per_gal  DOUBLE NOT NULL
    )
    """,
    """
    CREATE TABLE ref_detour (                        -- filled by routing in Wave 2, empty in Wave 1
        route_id           VARCHAR NOT NULL,
        station_id         VARCHAR NOT NULL,
        insert_after_stop  INTEGER NOT NULL,         -- StopSeq after which the station is inserted
        extra_miles        DOUBLE NOT NULL,
        extra_minutes      DOUBLE NOT NULL
    )
    """,
    """
    CREATE TABLE dq_log (
        check_name   VARCHAR NOT NULL,               -- e.g. ingest_fuel_card, dedupe_swipes, station_match, odometer_repair, timezone_telematics
        rows_in      BIGINT,
        rows_out     BIGINT,
        issue_count  BIGINT,
        notes        VARCHAR                          -- one plain-English sentence for the Data hub page
    )
    """,
    """
    CREATE TABLE dq_duplicate (                      -- added
        source_row       INTEGER NOT NULL,           -- the removed row
        kept_source_row  INTEGER NOT NULL,
        reason           VARCHAR NOT NULL
    )
    """,
    """
    CREATE TABLE dq_station_unmatched (              -- added
        source_row                 INTEGER NOT NULL,
        merchant_name              VARCHAR,
        best_candidate_station_id  VARCHAR,
        best_confidence            DOUBLE,
        reason                     VARCHAR NOT NULL
    )
    """,
    """
    CREATE TABLE eval_accuracy (                     -- added: scored against ground truth, only when it exists
        metric          VARCHAR NOT NULL,            -- station_match_precision | station_match_recall | dedupe_precision | dedupe_recall | odometer_repair_accuracy
        value           DOUBLE NOT NULL,             -- 0 to 1
        true_positive   BIGINT,
        false_positive  BIGINT,
        false_negative  BIGINT,
        notes           VARCHAR
    )
    """,
]

# Tables from the contract, in creation order (staging tables prefixed "stg_" are extra and temporary).
CONTRACT_TABLES: list[str] = [
    "dim_station",
    "dim_truck",
    "dim_route",
    "fact_fuel_txn",
    "fact_yard_fuel",
    "fact_ping",
    "fact_route_day",
    "ref_eia_weekly",
    "ref_station_price",
    "ref_detour",
    "dq_log",
    "dq_duplicate",
    "dq_station_unmatched",
    "eval_accuracy",
]

STAGING_PREFIX = "stg_"


def connect(path: str | Path) -> duckdb.DuckDBPyConnection:
    """Open (or create) the hub database file, creating its folder if needed."""
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path))


def create_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Drop every table and recreate the contract tables, so a rebuild always starts clean."""
    existing = con.execute(
        "SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = 'main'"
    ).fetchall()
    for name, table_type in existing:
        kind = "VIEW" if table_type == "VIEW" else "TABLE"
        con.execute(f'DROP {kind} IF EXISTS "{name}"')
    for statement in SCHEMA_DDL:
        con.execute(statement)


def log_dq(
    con: duckdb.DuckDBPyConnection,
    check_name: str,
    rows_in: int | None,
    rows_out: int | None,
    issue_count: int | None,
    notes: str,
) -> None:
    """Record one data-quality check result: how many rows went in, came out, and had issues."""
    con.execute(
        "INSERT INTO dq_log (check_name, rows_in, rows_out, issue_count, notes) VALUES (?, ?, ?, ?, ?)",
        [
            check_name,
            None if rows_in is None else int(rows_in),
            None if rows_out is None else int(rows_out),
            None if issue_count is None else int(issue_count),
            notes,
        ],
    )


def insert_frame(con: duckdb.DuckDBPyConnection, table: str, frame: pd.DataFrame) -> None:
    """Append a DataFrame to a contract table, matching columns by name (not by position)."""
    if frame.empty:
        return
    con.register("_insert_frame", frame)
    try:
        columns = ", ".join(f'"{c}"' for c in frame.columns)
        con.execute(f'INSERT INTO "{table}" BY NAME SELECT {columns} FROM _insert_frame')
    finally:
        con.unregister("_insert_frame")


def stage_frame(con: duckdb.DuckDBPyConnection, table: str, frame: pd.DataFrame) -> None:
    """Write a DataFrame as a staging table (replacing any older copy)."""
    con.register("_stage_frame", frame)
    try:
        con.execute(f'CREATE OR REPLACE TABLE "{table}" AS SELECT * FROM _stage_frame')
    finally:
        con.unregister("_stage_frame")


def read_table(con: duckdb.DuckDBPyConnection, table: str) -> pd.DataFrame:
    """Read a whole table into pandas."""
    return con.execute(f'SELECT * FROM "{table}"').df()


def drop_staging(con: duckdb.DuckDBPyConnection) -> None:
    """Remove the temporary staging tables once normalization has finished."""
    names = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' AND table_name LIKE 'stg\\_%' ESCAPE '\\'"
    ).fetchall()
    for (name,) in names:
        con.execute(f'DROP TABLE IF EXISTS "{name}"')

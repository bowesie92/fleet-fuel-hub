"""Ingest plus normalize on the hand-made fixtures: every contract table, the dq_log, and accuracy against ground truth."""

import json
import re
import shutil
from pathlib import Path

import pandas as pd
import pytest

from fleetfuel import config, db, ingest, normalize
from fleetfuel.normalize import evaluate

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pipeline"
IGNORE = shutil.ignore_patterns("make_fixtures.py", "__pycache__")


def build(raw_dir, db_path):
    cfg = config.load_assumptions()
    ingest.run(cfg, raw_dir=raw_dir, db_path=db_path, overrides_path=FIXTURES / "station_overrides.yaml")
    normalize.run(cfg, raw_dir=raw_dir, db_path=db_path)


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    path = tmp_path_factory.mktemp("hub") / "hub.duckdb"
    build(FIXTURES, path)
    con = db.connect(path)
    yield con
    con.close()


def rows(con, sql):
    return con.execute(sql).df()


def test_every_contract_table_exists_and_staging_is_gone(hub):
    tables = {r[0] for r in hub.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    assert tables == set(db.CONTRACT_TABLES)


def test_row_counts_on_the_fixtures(hub):
    counts = {t: hub.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in db.CONTRACT_TABLES}
    assert counts["dim_station"] == 4 and counts["dim_truck"] == 3 and counts["dim_route"] == 3
    assert counts["fact_fuel_txn"] == 16  # 18 swipes less 2 duplicates
    assert counts["fact_ping"] == 244 and counts["fact_route_day"] == 15
    assert counts["ref_eia_weekly"] == 2 and counts["ref_station_price"] == 20 and counts["ref_detour"] == 0
    assert counts["dq_duplicate"] == 2 and counts["dq_station_unmatched"] == 1 and counts["eval_accuracy"] == 5


def test_duplicates_removed_keep_the_first_swipe(hub):
    dups = rows(hub, "SELECT source_row, kept_source_row FROM dq_duplicate ORDER BY source_row")
    assert dups.values.tolist() == [[11, 10], [14, 13]]
    kept = set(rows(hub, "SELECT source_row FROM fact_fuel_txn")["source_row"])
    assert not ({11, 14} & kept) and {10, 13} <= kept
    # a second swipe a minute later for a different amount is a real second fill and stays
    assert {2, 3} <= kept


def test_utc_pings_are_converted_to_arizona_local_time(hub):
    first = rows(hub, "SELECT min(ts_local) AS t FROM fact_ping WHERE truck_id = 'TUC-01'")["t"][0]
    assert first == pd.Timestamp("2026-03-02 05:00:00")  # exported as 12:00 UTC, planned departure is 05:00 local
    raw_first = pd.read_csv(FIXTURES / "telematics.csv").query("device_id == 'GPS-20001'")["event_time"].min()
    assert raw_first.startswith("2026-03-02 12:00")


def test_odometers_are_repaired_and_flagged(hub):
    df = rows(hub, "SELECT source_row, odometer, odometer_repaired_flag FROM fact_fuel_txn").set_index("source_row")
    truth = pd.read_csv(FIXTURES / "ground_truth" / "fuel_txn_truth.csv").set_index("source_row")
    issue_rows = truth.index[(truth["odometer_issue"] != "none") & df.index.to_series().reindex(truth.index).notna()]
    assert len(issue_rows) == 5
    for r in issue_rows:
        assert df.loc[r, "odometer_repaired_flag"]
        assert abs(df.loc[r, "odometer"] - truth.loc[r, "true_odometer"]) <= 2.0
    assert df["odometer_repaired_flag"].sum() == 5  # good readings are left alone


def test_low_confidence_swipe_is_left_unmatched_with_a_reason(hub):
    fact = rows(hub, "SELECT station_id, station_match_confidence, station_match_method FROM fact_fuel_txn WHERE source_row = 18").iloc[0]
    assert pd.isna(fact["station_id"]) and pd.isna(fact["station_match_confidence"]) and fact["station_match_method"] == "unmatched"
    miss = rows(hub, "SELECT * FROM dq_station_unmatched").iloc[0]
    assert miss["source_row"] == 18 and miss["best_candidate_station_id"] == "STN-003" and miss["best_confidence"] < 0.7


def test_every_other_swipe_is_matched_to_the_right_station(hub):
    df = rows(hub, "SELECT source_row, station_id FROM fact_fuel_txn WHERE station_id IS NOT NULL").set_index("source_row")
    truth = pd.read_csv(FIXTURES / "ground_truth" / "fuel_txn_truth.csv").set_index("source_row")
    assert (df["station_id"] == truth.loc[df.index, "true_station_id"]).all()
    assert len(df) == 15


def test_dim_station_records_the_names_and_codes_that_matched(hub):
    ids = json.loads(rows(hub, "SELECT source_ids_json FROM dim_station WHERE station_id = 'STN-001'")["source_ids_json"][0])
    assert ids["osm_ref"] == "node/1001" and ids["merchant_codes"] == ["MERCH 004512"] and ids["merged_osm_refs"] == []
    merged = json.loads(rows(hub, "SELECT source_ids_json FROM dim_station WHERE station_id = 'STN-004'")["source_ids_json"][0])
    assert merged["merged_osm_refs"] == ["node/1005"]
    access = rows(hub, "SELECT truck_access, access_basis FROM dim_station WHERE station_id = 'STN-002'").iloc[0]
    assert (access["truck_access"], access["access_basis"]) == ("unverified", "truck_evidence")
    partner = rows(hub, "SELECT is_partner, partner_discount_cpg FROM dim_station WHERE station_id = 'STN-002'").iloc[0]
    assert bool(partner["is_partner"]) and partner["partner_discount_cpg"] == 12.0


def test_dim_route_and_route_days(hub):
    route = rows(hub, "SELECT * FROM dim_route WHERE route_id = 'TUC-R01'").iloc[0]
    assert route["yard"] == "TUC_YARD" and route["base_miles"] > 0 and route["base_minutes"] > 100
    assert '"label": "Customer 001"' in route["stops_json"] and '"label": "Tucson yard"' in route["stops_json"]
    assert route["geometry_geojson"].startswith('{"type": "LineString"')
    day = rows(hub, "SELECT actual_miles FROM fact_route_day WHERE route_id = 'TUC-R01' AND date = '2026-03-02'")["actual_miles"][0]
    assert 20 < day < 80


def test_dq_log_has_exactly_one_row_per_check(hub):
    checks = rows(hub, "SELECT check_name FROM dq_log")["check_name"]
    assert checks[checks != "station_override"].is_unique  # one row per check; station_override has one row per override record
    assert (checks == "station_override").sum() == 2
    expected = {"ingest_fuel_card", "ingest_telematics", "ingest_route_plans", "timezone_fuel_card", "timezone_telematics",
                "timezone_start_sanity", "dedupe_swipes", "station_match", "odometer_repair"}
    assert expected <= set(checks)
    dedupe_row = rows(hub, "SELECT * FROM dq_log WHERE check_name = 'dedupe_swipes'").iloc[0]
    assert (dedupe_row["rows_in"], dedupe_row["rows_out"], dedupe_row["issue_count"]) == (18, 16, 2)
    assert rows(hub, "SELECT count(*) AS n FROM dq_log WHERE notes IS NULL OR notes = ''")["n"][0] == 0


def test_start_sanity_finds_pings_line_up_with_planned_starts(hub):
    row = rows(hub, "SELECT * FROM dq_log WHERE check_name = 'timezone_start_sanity'").iloc[0]
    assert row["rows_in"] == 15 and row["issue_count"] == 0


def test_accuracy_against_fixture_ground_truth(hub):
    acc = rows(hub, "SELECT * FROM eval_accuracy").set_index("metric")
    assert acc.loc["station_match_precision", "value"] == 1.0           # 15 of 15 matched are right
    assert acc.loc["station_match_recall", "value"] == pytest.approx(15 / 16)  # one unreadable name left unmatched
    assert acc.loc["dedupe_precision", "value"] == 1.0
    assert acc.loc["dedupe_recall", "value"] == 1.0
    assert acc.loc["odometer_repair_accuracy", "value"] == 1.0
    assert acc.loc["station_match_recall", "false_negative"] == 1


def test_rebuilding_is_idempotent(tmp_path):
    path = tmp_path / "hub.duckdb"
    build(FIXTURES, path)
    con = db.connect(path)
    first = con.execute("SELECT count(*) FROM dq_log").fetchone()[0], con.execute("SELECT count(*) FROM fact_fuel_txn").fetchone()[0]
    con.close()
    build(FIXTURES, path)
    con = db.connect(path)
    try:
        assert (con.execute("SELECT count(*) FROM dq_log").fetchone()[0], con.execute("SELECT count(*) FROM fact_fuel_txn").fetchone()[0]) == first
        assert first[1] == 16
    finally:
        con.close()


def test_without_ground_truth_nothing_is_scored(tmp_path):
    raw = tmp_path / "raw"
    shutil.copytree(FIXTURES, raw, ignore=shutil.ignore_patterns("ground_truth", "make_fixtures.py", "__pycache__"))
    build(raw, tmp_path / "hub.duckdb")
    con = db.connect(tmp_path / "hub.duckdb")
    try:
        assert con.execute("SELECT count(*) FROM eval_accuracy").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM fact_fuel_txn").fetchone()[0] == 16  # cleaning never needed the truth
    finally:
        con.close()


# ---- evaluate.py: the metric arithmetic on a tiny hand-made case ------------------------------------------

def truth_frame():
    return pd.DataFrame({
        "source_row": [1, 2, 3, 4, 5],
        "true_event_id": ["E1", "E2", "E2", "E3", "E4"],
        "is_duplicate": [False, False, True, False, False],
        "true_station_id": ["S1", "S1", "S1", "S2", "S3"],
        "true_odometer": [100.0, 200.0, 200.0, 300.0, 400.0],
        "odometer_issue": ["none", "missing", "missing", "wrong", "none"],
    })


def test_station_match_precision_and_recall_arithmetic():
    facts = pd.DataFrame({"source_row": [1, 2, 4, 5], "station_id": ["S1", "S1", "S9", None]})  # row 4 wrong, row 5 unmatched
    got = {r["metric"]: r for r in evaluate.score_station_match(facts, truth_frame())}
    assert got["station_match_precision"]["value"] == pytest.approx(2 / 3)
    assert got["station_match_recall"]["value"] == pytest.approx(2 / 4)  # 4 non-duplicate real fills
    assert (got["station_match_precision"]["true_positive"], got["station_match_precision"]["false_positive"]) == (2, 1)


def test_dedupe_precision_and_recall_arithmetic():
    removed = pd.DataFrame({"source_row": [3, 4], "kept_source_row": [2, 3]})  # row 3 is a real copy, row 4 is not
    got = {r["metric"]: r for r in evaluate.score_dedupe(removed, truth_frame())}
    assert got["dedupe_precision"]["value"] == 0.5 and got["dedupe_recall"]["value"] == 1.0


def test_dedupe_removing_either_copy_of_a_pair_counts_as_correct():
    removed = pd.DataFrame({"source_row": [2], "kept_source_row": [3]})
    got = {r["metric"]: r for r in evaluate.score_dedupe(removed, truth_frame())}
    assert got["dedupe_precision"]["value"] == 1.0 and got["dedupe_recall"]["value"] == 1.0


def test_odometer_accuracy_arithmetic_uses_the_tolerance():
    facts = pd.DataFrame({"source_row": [1, 2, 4, 5], "odometer": [100.0, 201.5, 350.0, 400.0],
                          "odometer_repaired_flag": [False, True, True, True]})
    got = evaluate.score_odometer(facts, truth_frame(), tolerance_mi=2.0)[0]
    assert got["value"] == 0.5  # row 2 within 2 miles, row 4 not; rows with issues in the output: 2 and 4
    assert got["false_positive"] == 1  # row 5 had a good odometer but was changed


def test_station_override_rows_are_logged_in_plain_english(hub):
    notes = rows(hub, "SELECT notes FROM dq_log WHERE check_name = 'station_override' ORDER BY notes")["notes"].tolist()
    assert any("Merged STN-005 into STN-004" in n and "Status: applied" in n and "mapped twice" in n for n in notes)
    assert any("Excluded STN-099" in n and "not applied" in n for n in notes)


def test_swipe_at_a_merged_stations_old_location_matches_the_surviving_id():
    from fleetfuel import stations as station_module
    from fleetfuel.normalize import station_match

    cfg = config.load_assumptions()
    merged, _ = station_module.load_stations(FIXTURES, FIXTURES / "station_overrides.yaml")
    unmerged, _ = station_module.load_stations(FIXTURES, FIXTURES / "no_such_overrides.yaml")
    txns = pd.DataFrame({"source_row": [1], "truck_id": ["T1"], "ts_local": [pd.Timestamp("2026-03-02 10:00")],
                         "merchant_name": ["Copper Ridge Express"], "merchant_city": ["Casa Grande"]})
    old = (32.8830, -111.7450)  # where STN-005 used to be
    pings = pd.DataFrame({"truck_id": ["T1"] * 2, "ts_local": pd.to_datetime(["2026-03-02 09:58", "2026-03-02 10:08"]),
                          "lat": [old[0]] * 2, "lon": [old[1]] * 2, "odometer": [1.0, 1.0]})
    before = station_match.match_stations(txns, unmerged, pings, cfg).matches.iloc[0]["station_id"]
    after = station_match.match_stations(txns, merged, pings, cfg).matches.iloc[0]
    assert before == "STN-005"  # without the override the old pin wins
    assert after["station_id"] == "STN-004" and "STN-005" not in set(merged["station_id"])


def test_metric_with_no_cases_is_not_reported_as_perfect(tmp_path):
    truth = truth_frame().assign(is_duplicate=False, true_event_id=lambda d: [f"E{i}" for i in d["source_row"]])
    got = {r["metric"]: r["value"] for r in evaluate.score_dedupe(pd.DataFrame({"source_row": [], "kept_source_row": []}), truth)}
    assert got == {"dedupe_precision": None, "dedupe_recall": None}


def test_evaluate_skips_empty_metrics_and_logs_why(tmp_path):
    raw = tmp_path / "raw"
    shutil.copytree(FIXTURES, raw, ignore=IGNORE)
    truth_path = raw / "ground_truth" / "fuel_txn_truth.csv"
    t = pd.read_csv(truth_path)
    t["odometer_issue"] = "none"  # no odometer problems to score
    t.to_csv(truth_path, index=False, lineterminator="\n")
    build(raw, tmp_path / "hub.duckdb")
    con = db.connect(tmp_path / "hub.duckdb")
    try:
        metrics = set(con.execute("SELECT metric FROM eval_accuracy").fetchdf()["metric"])
        assert "odometer_repair_accuracy" not in metrics and len(metrics) == 4
        note = con.execute("SELECT notes FROM dq_log WHERE check_name = 'eval_odometer_repair_accuracy'").fetchone()[0]
        assert "no cases to score" in note
    finally:
        con.close()


def test_every_stop_has_the_first_plan_days_planned_arrival(hub):
    """Every stop of every route carries a valid HH:MM planned_arrival equal to the first plan day's PlannedArrival."""
    plans = pd.read_csv(FIXTURES / "route_plans.csv", dtype=str)
    first_day = plans.groupby("RouteID")["PlanDate"].transform("min")
    expected = plans[plans["PlanDate"] == first_day].set_index(["RouteID", "StopSeq"])["PlannedArrival"].to_dict()
    routes = rows(hub, "SELECT route_id, stops_json FROM dim_route")
    assert len(routes) == 3
    for route in routes.itertuples():
        for stop in json.loads(route.stops_json):
            assert re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", stop["planned_arrival"])
            assert stop["planned_arrival"] == expected[(route.route_id, str(stop["seq"]))]

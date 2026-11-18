"""A tiny hand-built hub for the analytics and export tests (no real data, never data/hub.duckdb).

Every number here is chosen so the expected answers can be worked out on paper; the arithmetic
is written in tests/test_analytics_handchecked.py. Loaded by path from the tests (there is no
conftest and no package, by contract section 1).

Cast of stations (all at one spot inside the Tucson box, so geometry never matters):
  STN-001 "A"  confirmed, not a partner: where the fills happen
  STN-002 "B"  confirmed, partner with a 10 cents a gallon discount: a cheaper alternative
  STN-003 "C"  UNVERIFIED truck access: cheaper still on one day
  STN-004 "S"  confirmed, not a partner: the partnership scout candidate
"""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

from fleetfuel import config, db

# route -> station -> (insert_after_stop, extra_miles, extra_minutes)
DETOURS = {
    "TUC-R01": {"STN-001": (1, 1.0, 3.0), "STN-002": (1, 2.0, 6.0), "STN-003": (1, 1.0, 3.0)},
    "TUC-R02": {"STN-001": (1, 1.0, 3.0), "STN-004": (1, 2.0, 4.0)},
    "TUC-R03": {"STN-001": (1, 0.5, 2.0), "STN-002": (1, 1.5, 5.0), "STN-003": (1, 1.0, 3.0)},
}

# date -> {station: posted price per gallon before any partner discount}
POSTED = {
    "2026-01-05": {"STN-001": 4.00, "STN-002": 3.90, "STN-003": 3.95, "STN-004": 3.95},  # fill F1
    "2026-01-06": {"STN-001": 3.95, "STN-002": 3.90, "STN-003": 3.95, "STN-004": 3.95},  # fill F2
    "2026-01-12": {"STN-001": 4.10, "STN-002": 3.95, "STN-003": 3.70, "STN-004": 3.95},  # fill F3
    "2026-01-08": {"STN-001": 4.20, "STN-002": 3.90, "STN-003": 4.50, "STN-004": 3.95},  # fill F4 (no fuel to reach B)
    "2026-01-09": {"STN-001": 3.92, "STN-002": 3.90, "STN-003": 4.50, "STN-004": 3.95},  # fill F5 (detour eats the saving)
    "2026-01-13": {"STN-001": 4.00, "STN-002": 3.90, "STN-003": 4.50, "STN-004": 3.95},  # scout fill X1 (at S)
    "2026-01-14": {"STN-001": 4.00, "STN-002": 3.90, "STN-003": 4.50, "STN-004": 3.95},  # scout fill X2 (at A)
    "2026-10-20": {"STN-001": 4.00, "STN-002": 3.90, "STN-003": 3.95, "STN-004": 3.95},  # inside the last 4 weeks: the test route price basis
}

# txn_id, truck, route, date, station, gallons, price paid per gallon
FILLS = [
    ("FC0000001", "TUC-01", "TUC-R01", "2026-01-05", "STN-001", 80.0, 4.000),   # F1
    ("FC0000002", "TUC-02", "TUC-R01", "2026-01-06", "STN-001", 80.0, 3.950),   # F2
    ("FC0000003", "TUC-03", "TUC-R03", "2026-01-12", "STN-001", 60.0, 4.100),   # F3
    ("FC0000004", "TUC-04", "TUC-R01", "2026-01-08", "STN-001", 110.0, 4.200),  # F4: only 4 gal on board
    ("FC0000005", "TUC-05", "TUC-R01", "2026-01-09", "STN-001", 50.0, 3.920),   # F5: net saving under threshold
    ("FC0000006", "TUC-06", "TUC-R02", "2026-01-13", "STN-004", 100.0, 3.950),  # X1: existing fill at the candidate
    ("FC0000007", "TUC-07", "TUC-R02", "2026-01-14", "STN-001", 80.0, 4.000),   # X2: fill that may shift to the candidate
]
MPG = 6.0
TANK_GAL = 120.0


def test_cfg() -> dict:
    """The real assumptions with the savings inputs pinned, so hand calculations never drift with config edits."""
    cfg = copy.deepcopy(config.load_assumptions())
    cfg["savings"].update({
        "driver_cost_per_hour": 38.0, "include_variable_cost": True, "variable_cost_per_mile": 0.25,
        "min_saving_threshold_usd": 5.0, "reserve_gal": 25, "typical_fill_gal": 72,
        "include_unverified_truck_access_default": False, "max_route_day_minutes": 840,
    })
    cfg["time_cost"].update({
        "peak_windows": ["06:00-09:00", "16:00-19:00"], "peak_weekdays_only": True, "travel_time_index": 1.17,
        "planning_time_index": 1.38, "use_reliability_buffer": False, "max_detour_minutes": 15,
        "schedule_risk_usd_per_minute": 0.50,
        "worked_example_detour_minutes": 12, "worked_example_detour_miles": 4, "worked_example_time": "07:00",
    })
    cfg["yards"]["phoenix_dc"]["onsite_fuel"] = False  # the mini hub has no yard fills; tests that need a tank switch it back on
    cfg["fueling"]["fill_to_tank_fraction"] = 0.95
    cfg["routes"]["minutes_per_stop"] = 25  # unloading time: a fuel stop after a customer happens 25 minutes after the planned arrival
    cfg["fleet"]["local_mpg"] = MPG
    cfg["period"].update({"start_date": "2025-11-01", "end_date": "2026-10-31"})
    return cfg


def build_mini_hub(path: Path, with_detours: bool = True) -> Path:
    """Create the hub tables with the cast above and return the path."""
    con = db.connect(path)
    db.create_schema(con)
    stations = [
        ("STN-001", "Alpha Fuel", None, "branded", False, 0.0, "confirmed", "truck_tag"),
        ("STN-002", "Bravo Fuel", None, "unbranded", True, 10.0, "confirmed", "truck_tag"),
        ("STN-003", "Charlie Fuel", None, "unbranded", False, 0.0, "unverified", "diesel_only"),
        ("STN-004", "Delta Fuel", None, "branded", False, 0.0, "confirmed", "truck_tag"),
    ]
    for sid, name, brand, klass, partner, cpg, access, basis in stations:
        con.execute(
            "INSERT INTO dim_station VALUES (?, ?, ?, 32.2, -110.9, ?, ?, ?, ?, ?, ?)",
            [sid, name, brand, klass, partner, cpg, json.dumps({"osm_ref": f"node/{sid}"}), access, basis],
        )
    for i in range(1, 8):
        con.execute("INSERT INTO dim_truck VALUES (?, 'TUC_YARD', 'local', ?, ?)", [f"TUC-0{i}", TANK_GAL, MPG])
    for route_id in DETOURS:
        stops = [{"seq": k, "location_id": loc, "label": lab, "lat": 32.14 + 0.01 * k, "lon": -110.96, "planned_arrival": arrival}
                 for k, (loc, lab, arrival) in enumerate([("TUC_YARD", "Tucson yard", "09:30"), ("CUST-0014", "Customer 014", "10:00"),
                                                          ("TUC_YARD", "Tucson yard", "11:00")])]  # all off-peak
        line = {"type": "LineString", "coordinates": [[s["lon"], s["lat"]] for s in stops]}
        con.execute("INSERT INTO dim_route VALUES (?, 'TUC_YARD', ?, 20.0, 300.0, ?)", [route_id, json.dumps(stops), json.dumps(line)])
    for n, (txn, truck, route, day, sid, gal, ppg) in enumerate(FILLS, start=1):
        con.execute("INSERT INTO fact_route_day VALUES (?, ?, ?, 20.0)", [route, truck, day])
        con.execute(
            "INSERT INTO fact_fuel_txn VALUES (?, ?, ?, ?, ?, ?, ?, 100000, false, ?, 0.99, 'name+brand+gps')",
            [txn, truck, sid, f"{day} 10:30:00", gal, ppg, round(gal * ppg, 2), n],  # 10:30 is outside the peak windows
        )
    for day, prices in POSTED.items():
        for sid, price in prices.items():
            con.execute("INSERT INTO ref_station_price VALUES (?, ?, ?)", [sid, day, price])
    for week, price in [("2025-12-29", 3.9), ("2026-01-05", 3.95), ("2026-01-12", 4.0)]:
        con.execute("INSERT INTO ref_eia_weekly VALUES (?, ?)", [week, price])
    if with_detours:
        for route, stations_ in DETOURS.items():
            for sid, (after, miles, minutes) in stations_.items():
                con.execute("INSERT INTO ref_detour VALUES (?, ?, ?, ?, ?)", [route, sid, after, miles, minutes])
    db.log_dq(con, "dedupe_swipes", 7, 7, 0, "Removed 0 duplicate card swipes.")
    con.execute("INSERT INTO eval_accuracy VALUES ('station_match_precision', 1.0, 7, 0, 0, '7 of 7 matched swipes went to the correct station.')")
    con.close()
    return Path(path)


def day(text: str) -> date:
    """ISO date text as a date."""
    return date.fromisoformat(text)

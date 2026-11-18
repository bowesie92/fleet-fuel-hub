"""One-off script that wrote the hand-made pipeline fixtures in this folder (kept for provenance).

Run `python tests/fixtures/pipeline/make_fixtures.py` to rewrite every file here. The data is
tiny and made up: five invented stations, three trucks, five days. Each event below is a
deliberate case from SPEC section 3, and the ground_truth/ files record exactly what was
injected so the tests can score the pipeline. Station names are fictional.
"""

from __future__ import annotations

import csv
import json
import math
import random
from datetime import datetime, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent
TRUTH = OUT / "ground_truth"
UTC_OFFSET_HOURS = 7  # Arizona is UTC-7 all year
CIRCUITY = 1.25
DAYS = [datetime(2026, 3, 2) + timedelta(days=i) for i in range(5)]
rng = random.Random(20261116)

YARDS = {"PHX_DC": (33.4180, -112.1550), "TUC_YARD": (32.1400, -110.9600)}
CUSTOMERS = {
    "CUST-0001": (32.2300, -110.9300), "CUST-0002": (32.2500, -110.8800),
    "CUST-0003": (32.2100, -110.9000), "CUST-0004": (32.1900, -110.9400),
    "CUST-0005": (32.2700, -110.9900), "CUST-0006": (32.2900, -111.0300),
    "CUST-0007": (32.2400, -111.0500), "CUST-0008": (32.2000, -111.0100),
}
# station_id: (name, brand, lat, lon, class, city, osm_ref, truck_tagged, truck_brand, merchant code)
STATIONS = {
    "STN-001": ("Desert Star Travel Center", "Desert Star", 32.4050, -111.2100, "travel_center", "Marana", "node/1001", True, True, "MERCH 004512", "confirmed", "truck_brand"),
    "STN-002": ("Saguaro Fuel & Go", "Saguaro Fuel", 32.2226, -110.9747, "branded", "Tucson", "node/1002", False, False, "MERCH 004513", "unverified", "truck_evidence"),
    "STN-003": ("Mesquite Mart", None, 32.1800, -110.9300, "cardlock", "Tucson", "way/1003", False, False, "MERCH 004514", "confirmed", "fleet_access"),
    "STN-004": ("Copper Ridge Fuel Stop", "Copper Ridge", 32.8800, -111.7500, "branded", "Casa Grande", "node/1004", True, False, "MERCH 004515", "confirmed", "truck_tag"),
    "STN-005": ("Copper Ridge Express", "Copper Ridge", 32.8830, -111.7450, "branded", "Casa Grande", "node/1005", False, False, "MERCH 004516", "confirmed", "truck_tag"),
}
PARTNERS = {"STN-002": 12.0}
STORE_NO = {"STN-001": 812, "STN-002": 4412, "STN-003": 118, "STN-004": 77, "STN-005": 78}
BASE_POSTED = {"STN-001": 3.999, "STN-002": 3.899, "STN-003": 3.759, "STN-004": 3.949, "STN-005": 3.929}

FLEET = [
    ("PHX-01", "PHX_DC", "linehaul", 200, 6.8, "GPS-10001", "1111"),
    ("TUC-01", "TUC_YARD", "local", 120, 6.0, "GPS-20001", "2201"),
    ("TUC-02", "TUC_YARD", "local", 120, 6.0, "GPS-20002", "2202"),
]
ROUTES = {
    "PHX-L01": ("PHX-01", ["PHX_DC", "TUC_YARD", "PHX_DC"], "02:00", 55.0, [0, 45, 0]),
    "TUC-R01": ("TUC-01", ["TUC_YARD", "CUST-0001", "CUST-0002", "CUST-0003", "CUST-0004", "TUC_YARD"], "05:00", 28.0, [0, 25, 25, 25, 25, 0]),
    "TUC-R02": ("TUC-02", ["TUC_YARD", "CUST-0005", "CUST-0006", "CUST-0007", "CUST-0008", "TUC_YARD"], "05:00", 28.0, [0, 25, 25, 25, 25, 0]),
}
START_ODOMETER = {"PHX-01": 250000.0, "TUC-01": 180000.0, "TUC-02": 150500.0}
FUEL_DWELL_MIN = 15
SWIPE_AFTER_ARRIVAL_MIN = 5
GAP_HALF_WIDTH_MIN = 60

# (truck, day index, station, insert after StopSeq, name variant, odometer issue, extra: none | dup1 | dup2 | split | gap)
EVENTS = [
    ("PHX-01", 0, "STN-001", 0, "merchant_code", "none", "none"),
    ("PHX-01", 1, "STN-001", 0, "merchant_code", "wrong", "none"),
    ("PHX-01", 2, "STN-001", 0, "merchant_code", "missing", "none"),
    ("PHX-01", 3, "STN-004", 0, "short_brand", "none", "none"),
    ("PHX-01", 4, "STN-004", 0, "canonical", "none", "gap"),
    ("TUC-01", 0, "STN-002", 2, "canonical", "none", "none"),
    ("TUC-01", 1, "STN-002", 2, "upper_store", "missing", "none"),
    ("TUC-01", 2, "STN-002", 2, "canonical", "none", "dup1"),
    ("TUC-01", 3, "STN-003", 2, "upper_store", "none", "none"),
    ("TUC-01", 4, "STN-003", 2, "unreadable", "none", "none"),
    ("TUC-02", 0, "STN-003", 1, "short_brand", "none", "split"),
    ("TUC-02", 1, "STN-002", 1, "canonical", "none", "none"),
    ("TUC-02", 2, "STN-002", 1, "upper_store", "wrong", "none"),
    ("TUC-02", 3, "STN-003", 1, "canonical", "none", "dup2"),
    ("TUC-02", 4, "STN-002", 1, "canonical", "missing", "none"),
]
UNREADABLE_NAME = "XQ RDRNR 77"


def miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Straight-line miles times the circuity factor."""
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 3958.8 * math.asin(math.sqrt(h)) * CIRCUITY


def point(location_id: str) -> tuple[float, float]:
    """Coordinates of a yard, customer or station."""
    if location_id in YARDS:
        return YARDS[location_id]
    if location_id in CUSTOMERS:
        return CUSTOMERS[location_id]
    s = STATIONS[location_id]
    return (s[2], s[3])


def simulate(stops: list[str], dwell: list[int], start: datetime, speed: float, odo0: float) -> dict:
    """Drive the stop list. Returns arrivals, departures, odometer at arrival and the moving segments."""
    t, odo, arrivals, departures, odos, segments = start, odo0, [], [], [], []
    for i, stop in enumerate(stops):
        arrivals.append(t)
        odos.append(odo)
        t_dep = t + timedelta(minutes=dwell[i])
        departures.append(t_dep)
        t = t_dep
        if i + 1 < len(stops):
            d = miles(point(stop), point(stops[i + 1]))
            t_end = t + timedelta(hours=d / speed)
            segments.append((t, t_end, point(stop), point(stops[i + 1]), odo, odo + d))
            t, odo = t_end, odo + d
    return {"arr": arrivals, "dep": departures, "odo": odos, "segments": segments, "end_odo": odo}


def pings_for(segments: list) -> list[tuple[datetime, float, float, float, int]]:
    """Irregular pings while moving: one at each end of every segment plus random ones between."""
    out = []
    for t0, t1, p0, p1, o0, o1 in segments:
        times = [t0, t1]
        t = t0 + timedelta(minutes=rng.randint(5, 25), seconds=rng.randint(0, 59))
        while t < t1 - timedelta(minutes=3):
            times.append(t)
            t += timedelta(minutes=rng.randint(5, 25), seconds=rng.randint(0, 59))
        for ts in sorted(times):
            ts = ts.replace(microsecond=0)
            f = (ts - t0).total_seconds() / max((t1 - t0).total_seconds(), 1)
            out.append((ts, p0[0] + f * (p1[0] - p0[0]), p0[1] + f * (p1[1] - p0[1]), o0 + f * (o1 - o0), 30))
    return out


def merchant_text(station_id: str, variant: str) -> str:
    """The messy name the card vendor printed for a station."""
    name, brand, *_rest = STATIONS[station_id]
    city = STATIONS[station_id][5]
    if variant == "canonical":
        return name
    if variant == "upper_store":
        return f"{(brand or name.split()[0]).upper()} #{STORE_NO[station_id]} {city.upper()}"
    if variant == "short_brand":
        return (brand or name.split()[0]).upper()
    if variant == "merchant_code":
        return STATIONS[station_id][9]
    return UNREADABLE_NAME


def main() -> None:
    """Write every fixture file."""
    TRUTH.mkdir(parents=True, exist_ok=True)
    plan_rows, ping_rows, swipes = [], [], []
    odometer = dict(START_ODOMETER)
    for route_id, (truck, stops, start_hm, speed, dwell) in ROUTES.items():
        for day_i, day in enumerate(DAYS):
            hh, mm = map(int, start_hm.split(":"))
            start = day.replace(hour=hh, minute=mm)
            planned = simulate(stops, dwell, start, speed, 0.0)
            for seq, loc in enumerate(stops):
                miles_prev = 0.0 if seq == 0 else miles(point(stops[seq - 1]), point(loc))
                lat, lon = point(loc)
                plan_rows.append({
                    "PlanDate": day.strftime("%Y-%m-%d"), "RouteID": route_id, "Vehicle": truck, "StopSeq": seq,
                    "StopType": "yard" if loc in YARDS else "customer", "LocationID": loc, "Lat": f"{lat:.5f}", "Lon": f"{lon:.5f}",
                    "PlannedArrival": planned["arr"][seq].strftime("%H:%M"), "PlannedMilesFromPrev": f"{miles_prev:.1f}",
                })
            event = next((e for e in EVENTS if e[0] == truck and e[1] == day_i), None)
            run_stops, run_dwell = list(stops), list(dwell)
            if event:
                run_stops.insert(event[3] + 1, event[2])
                run_dwell.insert(event[3] + 1, FUEL_DWELL_MIN)
            sim = simulate(run_stops, run_dwell, start, speed, odometer[truck])
            odometer[truck] = sim["end_odo"]
            day_pings = pings_for(sim["segments"])
            if event:
                fuel_idx = event[3] + 1
                swipe_ts = (sim["arr"][fuel_idx] + timedelta(minutes=SWIPE_AFTER_ARRIVAL_MIN)).replace(second=0, microsecond=0)
                true_odo = round(sim["odo"][fuel_idx], 1)
                if event[6] == "gap":
                    day_pings = [p for p in day_pings if abs((p[0] - swipe_ts).total_seconds()) > GAP_HALF_WIDTH_MIN * 60]
                swipes.append({"event": event, "route": route_id, "ts": swipe_ts, "true_odo": true_odo, "day": day})
            device = next(f[5] for f in FLEET if f[0] == truck)
            for ts, lat, lon, odo, spd in day_pings:
                utc = ts + timedelta(hours=UTC_OFFSET_HOURS)
                ping_rows.append((device, utc.strftime("%Y-%m-%d %H:%M:%S"), f"{lat:.5f}", f"{lon:.5f}", f"{odo:.1f}", spd))

    # Fuel card rows, truth and injected issues
    rows = []
    for n, s in enumerate(sorted(swipes, key=lambda x: (x["ts"], x["event"][0])), start=1):
        truck, _d, station, _after, variant, issue, extra = s["event"]
        card = next(f[6] for f in FLEET if f[0] == truck)
        tank_role = next(f[2] for f in FLEET if f[0] == truck)
        gallons = round(rng.uniform(85, 110) if tank_role == "linehaul" else rng.uniform(60, 90), 3)
        ppg = round(BASE_POSTED[station] - PARTNERS.get(station, 0.0) / 100, 3)
        if issue == "none":
            odo_text = f"{s['true_odo']:.1f}"
        elif issue == "missing":
            odo_text = ""
        else:
            odo_text = f"{s['true_odo'] + 4000:.1f}"
        base = {"truck": truck, "card": card, "ts": s["ts"], "station": station, "variant": variant, "issue": issue,
                "gallons": gallons, "ppg": ppg, "odo": odo_text, "true_odo": s["true_odo"], "route": s["route"], "event": f"EVT-{n:04d}"}
        rows.append({**base, "is_dup": False})
        if extra in ("dup1", "dup2"):
            minutes = 1 if extra == "dup1" else 2
            rows.append({**base, "ts": s["ts"] + timedelta(minutes=minutes), "is_dup": True})
        if extra == "split":
            rows.append({**base, "ts": s["ts"] + timedelta(minutes=1), "gallons": 14.5, "event": f"EVT-{n:04d}S", "is_dup": False})
    rows.sort(key=lambda r: (r["ts"], r["truck"], r["is_dup"]))

    card_rows, truth_rows, issues = [], [], []
    for i, r in enumerate(rows, start=1):
        txn = f"FC{i:07d}"
        total = round(r["gallons"] * r["ppg"], 2)
        card_rows.append({
            "TransactionID": txn, "CardLast4": r["card"], "Unit": r["truck"], "TransDate": r["ts"].strftime("%m/%d/%Y"),
            "TransTime": r["ts"].strftime("%H:%M"), "MerchantName": merchant_text(r["station"], r["variant"]),
            "MerchantCity": STATIONS[r["station"]][5] if r["variant"] != "unreadable" else "", "MerchantState": "AZ", "Product": "ULSD",
            "Gallons": f"{r['gallons']:.3f}", "PPG": f"{r['ppg']:.3f}", "Total": f"{total:.2f}", "Odometer": r["odo"],
        })
        truth_rows.append({
            "source_row": i, "TransactionID": txn, "true_event_id": r["event"], "is_duplicate": str(r["is_dup"]).lower(),
            "truck_id": r["truck"], "true_station_id": r["station"], "true_ts_local": r["ts"].strftime("%Y-%m-%d %H:%M:%S"),
            "true_odometer": f"{r['true_odo']:.1f}", "odometer_issue": r["issue"], "name_variant": "merchant_code" if r["variant"] == "unreadable" else r["variant"],
            "fill_behavior": "sensible", "route_id": r["route"],
        })
        if r["is_dup"]:
            issues.append(("duplicate_swipe", "fuel_card.csv", i, r["truck"], "", "", "Same swipe repeated within two minutes."))
        if r["issue"] == "missing":
            issues.append(("missing_odometer", "fuel_card.csv", i, r["truck"], "", "", "Odometer left blank."))
        if r["issue"] == "wrong":
            issues.append(("wrong_odometer", "fuel_card.csv", i, r["truck"], "", "", "Odometer mistyped by thousands of miles."))
        if r["variant"] != "canonical":
            shown = "merchant_code" if r["variant"] == "unreadable" else r["variant"]
            issues.append(("name_variant", "fuel_card.csv", i, r["truck"], "", "", f"Merchant name printed as {shown}."))
    for s in swipes:
        if s["event"][6] == "gap":
            lo = s["ts"] - timedelta(minutes=GAP_HALF_WIDTH_MIN)
            hi = s["ts"] + timedelta(minutes=GAP_HALF_WIDTH_MIN)
            issues.append(("gps_gap", "telematics.csv", "", s["event"][0], lo.strftime("%Y-%m-%d %H:%M:%S"), hi.strftime("%Y-%m-%d %H:%M:%S"), "No pings for two hours around a fill."))
    issues.append(("irregular_ping", "telematics.csv", "", "", "", "", "Pings arrive every 5 to 25 minutes, not on a fixed clock."))
    issues.append(("timezone_utc", "telematics.csv", "", "", "", "", "Event times are UTC, seven hours ahead of Arizona time."))

    def write(path: Path, header: list[str], data: list) -> None:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(header)
            w.writerows(data)

    write(OUT / "fleet.csv", ["truck_id", "home_yard", "role", "tank_gal", "mpg_assumed", "telematics_device_id", "fuel_card_last4"], FLEET)
    write(OUT / "partner_program.csv", ["station_id", "partner_discount_cpg"], list(PARTNERS.items()))
    price_rows = [(d.strftime("%Y-%m-%d"), sid, f"{BASE_POSTED[sid] + 0.004 * i:.3f}") for i, d in enumerate(DAYS) for sid in sorted(STATIONS)]
    write(OUT / "station_prices.csv", ["date", "station_id", "posted_price_per_gal"], price_rows)
    write(OUT / "eia_diesel.csv", ["week_start", "price_per_gal", "series_id"],
          [("2026-02-23", "3.812", "EMD_EPD2D_PTE_R5XCA_DPG"), ("2026-03-02", "3.845", "EMD_EPD2D_PTE_R5XCA_DPG")])
    write(OUT / "fuel_card.csv", list(card_rows[0]), [list(r.values()) for r in card_rows])
    ping_rows.sort(key=lambda p: (p[0], p[1]))
    write(OUT / "telematics.csv", ["device_id", "event_time", "lat", "lon", "odometer_mi", "speed_mph"], ping_rows)
    write(OUT / "route_plans.csv", list(plan_rows[0]), [list(r.values()) for r in sorted(plan_rows, key=lambda r: (r["PlanDate"], r["RouteID"], r["StopSeq"]))])
    features = []
    for sid in sorted(STATIONS):
        name, brand, lat, lon, cls, city, osm, tagged, tbrand, _code, access, basis = STATIONS[sid]
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                         "properties": {"station_id": sid, "osm_ref": osm, "name": name, "brand": brand, "operator": None, "city": city,
                                        "truck_tagged": tagged, "truck_brand": tbrand, "station_class": cls,
                                        "truck_access": access, "access_basis": basis}})
    with open(OUT / "stations.geojson", "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"type": "FeatureCollection", "features": features}, fh, indent=1)
        fh.write("\n")
    overrides = (
        "overrides:\n"
        '  - osm_ref: "node/1005"\n    station_id: "STN-005"\n    action: merge_into\n    target_osm_ref: "node/1004"\n'
        '    reason: "Same site mapped twice, about 0.4 miles apart in the fixture."\n'
        '  - osm_ref: "node/9999"\n    station_id: "STN-099"\n    action: exclude\n'
        '    reason: "Fixture entry for a station that is not in the file."\n'
    )
    (OUT / "station_overrides.yaml").write_text(overrides, encoding="utf-8", newline="\n")
    write(TRUTH / "fuel_txn_truth.csv", list(truth_rows[0]), [list(r.values()) for r in truth_rows])
    write(TRUTH / "injected_issues.csv", ["issue_id", "issue_type", "source_file", "source_row", "truck_id", "start_ts_local", "end_ts_local", "details"],
          [[f"ISS-{k:05d}", *row] for k, row in enumerate(issues, start=1)])
    manifest = {"seed": 20261116, "period_start": "2026-03-02", "period_end": "2026-03-06",
                "row_counts": {"fuel_card.csv": len(card_rows), "telematics.csv": len(ping_rows), "route_plans.csv": len(plan_rows)},
                "inputs": {"stations": "fixture", "customers": "fixture", "eia": "fixture"}}
    with open(TRUTH / "run_manifest.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=1)
        fh.write("\n")
    print(f"wrote {len(card_rows)} swipes, {len(ping_rows)} pings, {len(plan_rows)} planned stops")


if __name__ == "__main__":
    main()

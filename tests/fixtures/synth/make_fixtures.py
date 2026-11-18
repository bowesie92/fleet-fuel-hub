"""Rebuild the small synth fixtures in the exact contract 4.1 to 4.3 formats.

Run: uv run python tests/fixtures/synth/make_fixtures.py

Everything here is made up: station names are invented ("Sample Travel Plaza #12"), customer
points are random coordinates inside the Tucson box, and the diesel prices are a seeded random
walk. None of it describes a real place or a real price.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
FIXTURE_SEED = 7
CUSTOMER_COUNT = 120
CUSTOMER_BOX = (32.10, -111.05, 32.35, -110.80)  # south, west, north, east
EIA_FIRST_WEEK = date(2022, 10, 31)
EIA_LAST_WEEK = date(2026, 10, 26)

# (name, brand, city, class, lat, lon): made-up stations spread over Phoenix, the I-10 corridor and Tucson
STATIONS = [
    ("Sample Travel Plaza #12", "Sample Travel Plaza", "Phoenix", "travel_center", 33.4390, -112.1900),
    ("Sample Fuel Stop #3", "Sample Fuel", "Phoenix", "branded", 33.4010, -112.1300),
    ("Corner Pump West", None, "Phoenix", "unbranded", 33.4550, -112.2300),
    ("Sample Fuel Stop #8", "Sample Fuel", "Chandler", "branded", 33.3000, -111.9000),
    ("Sample Travel Plaza #21", "Sample Travel Plaza", "Casa Grande", "travel_center", 32.8800, -111.7500),
    ("Desert Road Fuel", None, "Casa Grande", "unbranded", 32.8700, -111.7300),
    ("Sample Fuel Stop #14", "Sample Fuel", "Eloy", "branded", 32.7500, -111.5500),
    ("Sample Travel Plaza #30", "Sample Travel Plaza", "Marana", "travel_center", 32.4300, -111.2200),
    (None, "Sample Fuel", "Marana", "branded", 32.4000, -111.1800),
    ("Sample Travel Plaza #44", "Sample Travel Plaza", "Tucson", "travel_center", 32.1200, -110.9800),
    ("Sample Fuel Stop #51", "Sample Fuel", "Tucson", "branded", 32.1700, -110.9400),
    ("South Side Gas", None, "Tucson", "unbranded", 32.1000, -110.9700),
    ("Sample Fuel Stop #52", "Sample Fuel", "Tucson", "branded", 32.2300, -110.9300),
    ("Midtown Pump", None, None, "unbranded", 32.2500, -110.9800),
    ("Sample Fuel Stop #60", "Sample Fuel", "Tucson", "branded", 32.3000, -111.0000),
    (None, None, "Tucson", "unbranded", 32.2000, -111.0300),
    ("Sample Travel Plaza #61", "Sample Travel Plaza", "Tucson", "travel_center", 32.0600, -110.9100),
    ("Sample Fuel Stop #70", "Sample Fuel", "Tucson", "branded", 32.2100, -110.8600),
    ("East Side Fuel", None, "Tucson", "unbranded", 32.2400, -110.8300),
    ("Sample Fuel Stop #77", "Sample Fuel", "Tucson", "branded", 32.3300, -110.9500),
    # a same-site pair: the second entry is the same station mapped twice (merged by an override)
    ("Sample Fuel Stop #90", "Sample Fuel", "Tucson", "branded", 32.1500, -110.9900),
    (None, "Sample Fuel", None, "branded", 32.1501, -110.9901),
    # a truck scale mapped as a fuel station (excluded by an override)
    ("Sample Scale House", None, "Tucson", "unbranded", 32.1300, -110.9500),
]

# Truck access per station name: (truck_access, access_basis, class override). Default is confirmed by truck tag.
ACCESS = {
    "South Side Gas": ("unverified", "truck_evidence", None),
    "Midtown Pump": ("unverified", "truck_evidence", None),
    "East Side Fuel": ("confirmed", "cardlock", "cardlock"),
}


def write_stations() -> None:
    """Write stations.geojson with ids assigned in order of osm_ref sorted as text (contract 4.2)."""
    refs = sorted(f"node/{9000001 + i}" for i in range(len(STATIONS)))
    features = []
    for ref, (name, brand, city, klass, lat, lon) in zip(refs, STATIONS):
        access, basis, klass_override = ACCESS.get(name, ("confirmed", "truck_brand" if klass == "travel_center" else "truck_tag", None))
        klass = klass_override or klass
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
            "properties": {
                "station_id": f"STN-{len(features) + 1:03d}", "osm_ref": ref, "name": name, "brand": brand,
                "operator": None, "city": city, "truck_tagged": basis == "truck_tag",
                "truck_brand": klass == "travel_center", "station_class": klass,
                "truck_access": access, "access_basis": basis,
            },
        })
    (HERE / "stations.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, indent=1) + "\n", encoding="utf-8", newline="\n")


def write_overrides() -> None:
    """Write station_overrides.yaml for the fixtures: exclude the scale, merge the same-site pair (contract 4.11)."""
    refs = sorted(f"node/{9000001 + i}" for i in range(len(STATIONS)))
    ref_of = {name: ref for ref, (name, *_rest) in zip(refs, STATIONS) if name}
    merged_ref = refs[len(STATIONS) - 2]
    keep_ref = ref_of["Sample Fuel Stop #90"]
    lines = [
        "overrides:",
        f"  - osm_ref: {ref_of['Sample Scale House']}",
        "    action: exclude",
        "    reason: truck scale, not a fuel station",
        f"  - osm_ref: {merged_ref}",
        "    action: merge_into",
        f"    target_osm_ref: {keep_ref}",
        "    reason: same site mapped twice",
    ]
    nl = chr(10)
    (HERE / "station_overrides.yaml").write_text(nl.join(lines) + nl, encoding="utf-8", newline=nl)


def write_customers() -> None:
    """Write customer_points.csv: random anonymous coordinates, no names (contract 4.3)."""
    rng = np.random.default_rng(FIXTURE_SEED)
    south, west, north, east = CUSTOMER_BOX
    lat = rng.uniform(south, north, CUSTOMER_COUNT).round(5)
    lon = rng.uniform(west, east, CUSTOMER_COUNT).round(5)
    lines = ["customer_id,lat,lon"] + [f"CUST-{i + 1:04d},{lat[i]:.5f},{lon[i]:.5f}" for i in range(CUSTOMER_COUNT)]
    (HERE / "customer_points.csv").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def write_eia() -> None:
    """Write eia_diesel.csv: weekly Mondays as a seeded random walk (contract 4.1)."""
    rng = np.random.default_rng(FIXTURE_SEED + 1)
    weeks = (EIA_LAST_WEEK - EIA_FIRST_WEEK).days // 7 + 1
    steps = rng.normal(0.0, 0.04, weeks)
    price = 4.20 + np.cumsum(steps) - 0.002 * np.arange(weeks)
    lines = ["week_start,price_per_gal,series_id"]
    for i in range(weeks):
        lines.append(f"{EIA_FIRST_WEEK + timedelta(weeks=i)},{price[i]:.3f},SAMPLE_SERIES_W")
    (HERE / "eia_diesel.csv").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    write_stations()
    write_overrides()
    write_customers()
    write_eia()
    print("fixtures written to", HERE)

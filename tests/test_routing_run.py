"""Routing end to end with a fake openrouteservice: caching, no-key behavior and the database write."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from fleetfuel import config, db, routing, stations
from fleetfuel.routing import detours
from fleetfuel.routing.client import OrsClient

STOPS = [
    {"seq": 0, "location_id": "TUC_YARD", "label": "Tucson yard", "lat": 32.10, "lon": -111.00},
    {"seq": 1, "location_id": "CUST-0001", "label": "Customer 001", "lat": 32.20, "lon": -111.00},
    {"seq": 2, "location_id": "CUST-0002", "label": "Customer 002", "lat": 32.30, "lon": -111.00},
    {"seq": 3, "location_id": "PHX_DC", "label": "Phoenix DC", "lat": 32.40, "lon": -111.00},
]
STATIONS = pd.DataFrame(
    {
        "station_id": ["STN-001", "STN-002", "STN-003"],
        "lat": [32.21, 32.29, 40.0],  # the last one is far from every route
        "lon": [-111.01, -111.01, -100.0],
    }
)


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


class FakeOrs:
    """Stands in for requests.post: road distance is 1.3 x the straight line."""

    def __init__(self):
        self.calls = []
        self.headers = []

    def __call__(self, url, json, headers, timeout):
        self.calls.append((url, json))
        self.headers.append(headers)
        if "/matrix/" in url:
            pts = json["locations"]
            d = [[detours.haversine_mi(a[1], a[0], b[1], b[0]) * detours.METERS_PER_MILE * 1.3 for b in pts] for a in pts]
            t = [[v / 20.0 for v in row] for row in d]
            return FakeResponse({"distances": d, "durations": t})
        pts = json["coordinates"]
        total = sum(
            detours.haversine_mi(a[1], a[0], b[1], b[0]) * detours.METERS_PER_MILE * 1.3 for a, b in zip(pts, pts[1:])
        )
        feature = {
            "geometry": {"type": "LineString", "coordinates": pts},
            "properties": {"summary": {"distance": total, "duration": total / 20.0}},
        }
        return FakeResponse({"features": [feature]})


@pytest.fixture
def cfg():
    return config.load_assumptions()


@pytest.fixture
def hub(tmp_path, monkeypatch):
    path = tmp_path / "hub.duckdb"
    con = db.connect(path)
    db.create_schema(con)
    con.execute(
        "INSERT INTO dim_route VALUES (?, ?, ?, ?, ?, ?)",
        ["TUC-R01", "TUC_YARD", json.dumps(STOPS), 1.0, 1.0, "{}"],
    )
    con.execute(
        "INSERT INTO dim_route VALUES (?, ?, ?, ?, ?, ?)",
        ["TUC-R02", "TUC_YARD", json.dumps(STOPS), 1.0, 1.0, "{}"],
    )
    con.close()
    monkeypatch.setattr(stations, "load_stations", lambda raw_dir=None, overrides_path=None: (STATIONS.copy(), []))
    # Offline and key-independent: never read the real .env, and give the client a dummy key so the
    # fake openrouteservice is used the same way on a laptop with a key and in CI without one.
    monkeypatch.setattr(config, "load_env", lambda: None)
    monkeypatch.setenv("ORS_API_KEY", "dummy-test-key")
    return path


def test_run_fills_dim_route_and_ref_detour_and_second_run_is_free(cfg, hub, tmp_path):
    cache = tmp_path / "ors"
    fake = FakeOrs()
    routing.run(cfg, db_path=hub, cache_dir=cache, client=OrsClient(cfg, cache, post=fake, sleep=lambda s: None))
    # Two identical routes share one directions call and one matrix call.
    kinds = [("matrix" if "/matrix/" in u else "directions") for u, _ in fake.calls]
    assert kinds.count("directions") == 1 and kinds.count("matrix") == 1

    con = db.connect(hub)
    rows = con.execute("SELECT * FROM ref_detour ORDER BY route_id, station_id").df()
    route = con.execute("SELECT base_miles, base_minutes, geometry_geojson FROM dim_route WHERE route_id = 'TUC-R01'").fetchone()
    con.close()
    assert set(rows["route_id"]) == {"TUC-R01", "TUC-R02"}
    assert set(rows["station_id"]) == {"STN-001", "STN-002"}  # the far station is outside the radius
    stn1 = rows[(rows.route_id == "TUC-R01") & (rows.station_id == "STN-001")].iloc[0]
    assert stn1["insert_after_stop"] == 1  # between customers 1 and 2, where the station sits
    assert stn1["extra_miles"] > 0
    assert route[0] > 20  # real road miles replaced the placeholder
    minutes = cfg["routes"]["minutes_per_stop"] * 2
    assert route[1] > minutes
    assert json.loads(route[2])["type"] == "LineString"

    again = FakeOrs()
    routing.run(cfg, db_path=hub, cache_dir=cache, client=OrsClient(cfg, cache, post=again, sleep=lambda s: None))
    assert again.calls == []


def test_cache_has_no_key_and_survives_host_change(cfg, hub, tmp_path, monkeypatch):
    monkeypatch.setenv("ORS_API_KEY", "secret-test-key")
    cache = tmp_path / "ors"
    fake = FakeOrs()
    routing.run(cfg, db_path=hub, cache_dir=cache, client=OrsClient(cfg, cache, post=fake, sleep=lambda s: None))
    assert fake.headers[0]["Authorization"] == "secret-test-key"
    for f in cache.glob("*.json"):
        text = f.read_text(encoding="utf-8")
        assert "secret-test-key" not in text and "heigit" not in text

    other = json.loads(json.dumps(cfg))
    other["routing"]["ors_base_url"] = "https://example.invalid"
    again = FakeOrs()
    routing.run(other, db_path=hub, cache_dir=cache, client=OrsClient(other, cache, post=again, sleep=lambda s: None))
    assert again.calls == []


def test_cache_miss_without_key_fails_plainly(cfg, hub, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "load_env", lambda: None)
    monkeypatch.delenv("ORS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ORS_API_KEY"):
        routing.run(cfg, db_path=hub, cache_dir=tmp_path / "empty", client=OrsClient(cfg, tmp_path / "empty", post=FakeOrs()))


def test_route_geometry_and_detour_path(cfg, hub, tmp_path):
    cache = tmp_path / "ors"
    client = OrsClient(cfg, cache, post=FakeOrs(), sleep=lambda s: None)
    line = routing.route_geometry(cfg, "TUC-R01", db_path=hub, client=client)
    assert line["type"] == "LineString" and len(line["coordinates"]) == len(STOPS)
    path = routing.detour_path(cfg, "TUC-R01", "STN-001", 1, db_path=hub, client=client)
    assert len(path["coordinates"]) == len(STOPS) + 1
    assert path["coordinates"][2] == [-111.01, 32.21]  # station sits right after stop 1
    with pytest.raises(KeyError):
        routing.detour_path(cfg, "TUC-R01", "STN-999", 1, db_path=hub, client=client)


def test_matrix_stays_under_cell_limit(cfg, hub, tmp_path):
    fake = FakeOrs()
    routing.run(cfg, db_path=hub, cache_dir=tmp_path / "ors", client=OrsClient(cfg, tmp_path / "ors", post=fake, sleep=lambda s: None))
    for url, body in fake.calls:
        if "/matrix/" in url:
            assert len(body["locations"]) ** 2 <= cfg["routing"]["max_matrix_cells"]

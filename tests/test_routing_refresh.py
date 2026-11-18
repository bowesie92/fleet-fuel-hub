"""Snapping fallback, refresh_cache and pruning, all offline with a fake openrouteservice."""

from __future__ import annotations

import importlib
import json

import pandas as pd
import pytest

from fleetfuel import cli, config, db, routing, stations
from fleetfuel.routing import detours
from fleetfuel.routing.client import OrsClient


@pytest.fixture(autouse=True)
def no_real_key(monkeypatch):
    """Every test here uses a fake HTTP post: give the client a dummy key and never load the real .env (hard rule 12),
    so the tests behave the same on a laptop with a .env and in CI without one."""
    monkeypatch.setattr(config, "load_env", lambda: None)
    monkeypatch.setenv("ORS_API_KEY", "dummy-test-key")

YARD = (32.10, -111.00)
OFF_ROAD = (32.20, -111.00)  # a moved customer point that sits off the road network
STOPS = [
    {"seq": 0, "location_id": "TUC_YARD", "label": "Tucson yard", "lat": YARD[0], "lon": YARD[1]},
    {"seq": 1, "location_id": "CUST-0001", "label": "Customer 001", "lat": OFF_ROAD[0], "lon": OFF_ROAD[1]},
    {"seq": 2, "location_id": "PHX_DC", "label": "Phoenix DC", "lat": 32.40, "lon": -111.00},
]
STATIONS = pd.DataFrame({"station_id": ["STN-001"], "lat": [32.21], "lon": [-111.01]})
MESSAGE = "Could not find routable point within a radius of 350.0 meters of specified coordinate {i}: x."


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeOrs:
    """Road = 1.3 x straight line. The off-road point is refused unless the request carries `radiuses` (directions only)."""

    def __init__(self):
        self.calls = []

    @staticmethod
    def _bad(pt):
        return [round(pt[0], 6), round(pt[1], 6)] == [OFF_ROAD[1], OFF_ROAD[0]]

    def __call__(self, url, json, headers, timeout):
        self.calls.append((url, json))
        key = "locations" if "/matrix/" in url else "coordinates"
        pts = json[key]
        for i, pt in enumerate(pts):
            if self._bad(pt) and "radiuses" not in json:
                code = 6010 if key == "locations" else 2010
                return FakeResponse({"error": {"code": code, "message": MESSAGE.format(i=i)}}, status=404)

        def dist(a, b):
            return detours.haversine_mi(a[1], a[0], b[1], b[0]) * detours.METERS_PER_MILE * 1.3

        if key == "locations":
            d = [[dist(a, b) for b in pts] for a in pts]
            return FakeResponse({"distances": d, "durations": [[v / 20.0 for v in r] for r in d]})
        total = sum(dist(a, b) for a, b in zip(pts, pts[1:]))
        feature = {"geometry": {"type": "LineString", "coordinates": pts},
                   "properties": {"summary": {"distance": total, "duration": total / 20.0}}}
        return FakeResponse({"features": [feature]})


def make_client(cfg, cache, fake):
    return OrsClient(cfg, cache, post=fake, sleep=lambda s: None)


@pytest.fixture
def cfg():
    return config.load_assumptions()


@pytest.fixture
def hub(tmp_path, monkeypatch):
    path = tmp_path / "hub.duckdb"
    con = db.connect(path)
    db.create_schema(con)
    con.execute("INSERT INTO dim_route VALUES (?, ?, ?, ?, ?, ?)", ["TUC-R01", "TUC_YARD", json.dumps(STOPS), 1.0, 1.0, "{}"])
    con.close()
    monkeypatch.setattr(stations, "load_stations", lambda raw_dir=None, overrides_path=None: (STATIONS.copy(), []))
    monkeypatch.setattr(config, "load_env", lambda: None)
    monkeypatch.setenv("ORS_API_KEY", "dummy-test-key")
    return path


def test_fallback_only_on_no_routable_point_and_cache_read_first(cfg, tmp_path):
    cache = tmp_path / "ors"
    fake = FakeOrs()
    client = make_client(cfg, cache, fake)
    client.directions([YARD, (32.40, -111.00)])
    assert len(fake.calls) == 1 and "radiuses" not in fake.calls[0][1]  # no error, no retry

    coords = [YARD, OFF_ROAD]
    client.directions(coords)
    assert len(fake.calls) == 3  # refused, then retried once
    assert fake.calls[2][1]["radiuses"] == [cfg["routing"]["snap_retry_radius_m"]] * 2

    fresh = FakeOrs()  # the next build: everything is in the cache
    again = make_client(cfg, cache, fresh)
    again.directions(coords)
    assert fresh.calls == [] and again.cached["directions"] == 1


def test_other_errors_are_not_retried(cfg, tmp_path):
    def broken(url, json, headers, timeout):
        return FakeResponse({"error": {"code": 500, "message": "boom"}}, status=500)

    client = make_client(cfg, tmp_path / "ors", broken)
    with pytest.raises(RuntimeError, match="HTTP 500"):
        client.directions([YARD, OFF_ROAD])
    assert client.live["directions"] == 1


def test_matrix_unsnappable_point_becomes_unroutable(cfg, tmp_path):
    fake = FakeOrs()
    pts = [YARD, OFF_ROAD, (32.40, -111.00)]
    grid = make_client(cfg, tmp_path / "ors", fake).matrix(pts)
    assert grid["meters"][1][0] is None and grid["seconds"][0][1] is None
    assert grid["meters"][0][2] > 0
    assert all("radiuses" not in body for _, body in fake.calls)
    again = FakeOrs()
    make_client(cfg, tmp_path / "ors", again).matrix(pts)
    assert again.calls == []


def test_refresh_counts_and_prune_removes_only_unused(cfg, hub, tmp_path):
    cache = tmp_path / "ors"
    cache.mkdir()
    stale = cache / "directions_deadbeefdeadbeef.json"
    stale.write_text("{}", encoding="utf-8")
    fake = FakeOrs()
    result = routing.refresh_cache(cfg, prune=True, db_path=hub, cache_dir=cache, client=make_client(cfg, cache, fake))
    assert not stale.exists() and result["pruned"] == 1
    kinds = [("matrix" if "/matrix/" in u else "directions") for u, _ in fake.calls]
    assert result["live_directions"] == kinds.count("directions") and result["live_matrix"] == kinds.count("matrix")
    assert result["failed"] == 0
    kept = sorted(cache.glob("*.json"))
    assert kept

    again = FakeOrs()
    second = routing.refresh_cache(cfg, prune=True, db_path=hub, cache_dir=cache, client=make_client(cfg, cache, again))
    assert again.calls == [] and second["pruned"] == 0 and second["cache_hits"] > 0
    assert sorted(cache.glob("*.json")) == kept


def test_prune_off_keeps_everything(cfg, hub, tmp_path):
    cache = tmp_path / "ors"
    cache.mkdir()
    stale = cache / "matrix_deadbeefdeadbeef.json"
    stale.write_text("{}", encoding="utf-8")
    result = routing.refresh_cache(cfg, db_path=hub, cache_dir=cache, client=make_client(cfg, cache, FakeOrs()))
    assert stale.exists() and result["pruned"] == 0


def test_build_never_calls_refresh_cache(cfg, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("refresh_cache must not run in the build")

    monkeypatch.setattr(routing, "refresh_cache", boom)
    seen = []

    class Step:
        def __init__(self, name):
            self.name = name

        def run(self, cfg):
            seen.append(self.name)

    monkeypatch.setattr(importlib, "import_module", lambda name: Step(name))
    cli.cmd_build(cfg)
    assert "fleetfuel.routing" in seen
    assert "refresh_cache" not in open(cli.__file__, encoding="utf-8").read()


def test_pause_is_shared_by_every_client_in_the_run(tmp_path, monkeypatch):
    """Export makes a new client per detour leg; the pause between live calls must still hold across them."""
    monkeypatch.setenv("ORS_API_KEY", "test-key")
    monkeypatch.setattr(OrsClient, "_last_live_at", None)
    cfg = config.load_assumptions()
    slept: list[float] = []
    post = lambda *a, **k: FakeResponse({"ok": 1})  # noqa: E731
    first = OrsClient(cfg, tmp_path, post=post, sleep=slept.append)
    second = OrsClient(cfg, tmp_path, post=post, sleep=slept.append)
    first.request("directions", "/v2/directions/{profile}/geojson", {"coordinates": [[1, 1], [2, 2]]})
    second.request("directions", "/v2/directions/{profile}/geojson", {"coordinates": [[3, 3], [4, 4]]})
    assert len(slept) == 1 and 0 < slept[0] <= first.pause_s


def test_rate_limited_call_waits_and_retries(tmp_path, monkeypatch):
    """HTTP 429 (too many calls this minute) waits rate_limit_wait_s and tries again instead of failing."""
    monkeypatch.setenv("ORS_API_KEY", "test-key")
    monkeypatch.setattr(OrsClient, "_last_live_at", None)
    cfg = config.load_assumptions()
    answers = [FakeResponse({"error": "Rate Limit Exceeded"}, status=429), FakeResponse({"ok": 1})]
    slept: list[float] = []
    client = OrsClient(cfg, tmp_path, post=lambda *a, **k: answers.pop(0), sleep=slept.append)
    assert client.request("directions", "/v2/directions/{profile}/geojson", {"coordinates": [[5, 5], [6, 6]]}) == {"ok": 1}
    assert client.rate_limit_wait_s in slept and not answers


def test_backoff_uses_retry_after_and_otherwise_doubles(tmp_path, monkeypatch):
    client = OrsClient(config.load_assumptions(), tmp_path, post=lambda *a, **k: None, sleep=lambda s: None)
    told = FakeResponse({"error": "Rate Limit Exceeded"}, status=429)
    told.headers = {"Retry-After": "12"}
    assert client.backoff_s(told, 0) == 12.0
    silent = FakeResponse({"error": "Rate Limit Exceeded"}, status=429)
    assert [client.backoff_s(silent, n) for n in (0, 1)] == [client.rate_limit_wait_s, client.rate_limit_wait_s * 2]


def test_gives_up_after_the_configured_tries(tmp_path, monkeypatch):
    """Three 429s in a row: three tries in all, then a plain error the warm-up counts as failed."""
    monkeypatch.setenv("ORS_API_KEY", "test-key")
    monkeypatch.setattr(OrsClient, "_last_live_at", None)
    calls = []
    def post(*a, **k):
        calls.append(1)
        return FakeResponse({"error": "Rate Limit Exceeded"}, status=429)
    client = OrsClient(config.load_assumptions(), tmp_path, post=post, sleep=lambda s: None)
    with pytest.raises(RuntimeError, match="429"):
        client.request("directions", "/v2/directions/{profile}/geojson", {"coordinates": [[7, 7], [8, 8]]})
    assert len(calls) == client.rate_limit_tries == 3

"""Contract 15.2: long Retry-After is not slept on, every attempt counts, refused retries are cached. All offline."""

from __future__ import annotations

import pytest

from fleetfuel import config, db, routing
from fleetfuel.routing.client import OrsClient
from test_routing_run import FakeOrs, FakeResponse, cfg, hub  # noqa: F401  (fixtures reused)

PATH = "/v2/directions/{profile}/geojson"
BAD_STATION = [-111.01, 32.21]  # STN-001 as [lon, lat]; the fake refuses it even with a wider radius
INSERT = ("INSERT INTO ref_detour (route_id, station_id, insert_after_stop, extra_miles, extra_minutes) "
          "VALUES ('TUC-R01', ?, 1, 1, 1)")


def _client(tmp_path, post, sleep=lambda s: None):
    return OrsClient(config.load_assumptions(), tmp_path / "ors", post=post, sleep=sleep)


def _too_many(retry_after=None):
    resp = FakeResponse({"error": "Rate Limit Exceeded"})
    resp.status_code = 429
    resp.text = "Rate Limit Exceeded"
    resp.headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    return resp


class RefusesStation(FakeOrs):
    """Like FakeOrs, but directions through the bad station are always refused (point not found), radius or not."""

    def __call__(self, url, json, headers, timeout):
        if "/directions/" in url and BAD_STATION in [[round(a, 6), round(b, 6)] for a, b in json["coordinates"]]:
            self.calls.append((url, json))
            text = '{"error": {"code": 2010, "message": "Could not find routable point: coordinate 1"}}'
            resp = FakeResponse({"error": {"code": 2010, "message": "Could not find routable point: coordinate 1"}})
            resp.status_code = 404
            resp.text = text
            return resp
        return super().__call__(url, json, headers, timeout)


def test_long_retry_after_is_not_slept_on(tmp_path, monkeypatch):
    monkeypatch.setenv("ORS_API_KEY", "test-key")
    monkeypatch.setattr(OrsClient, "_last_live_at", None)
    slept: list[float] = []
    calls = []

    def post(*a, **k):
        calls.append(1)
        return _too_many(retry_after=3600)

    client = _client(tmp_path, post, slept.append)
    with pytest.raises(RuntimeError, match="daily quota may be used up"):
        client.request("directions", PATH, {"coordinates": [[1, 1], [2, 2]]})
    assert len(calls) == 1 and not any(s >= 3600 for s in slept)


def test_warm_counts_a_long_retry_after_as_failed(cfg, hub, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(OrsClient, "_last_live_at", None)
    con = db.connect(hub)
    con.execute(INSERT, ["STN-001"])
    con.close()
    client = _client(tmp_path, lambda *a, **k: _too_many(retry_after=3600))
    out = routing.warm_detour_leg_cache(cfg, db_path=hub, client=client)
    assert len(out["failed"]) == 1 and "quota" in out["failed"][0][3] and not out["unroutable"]


def test_every_attempt_counts_toward_the_daily_total(tmp_path, monkeypatch):
    monkeypatch.setenv("ORS_API_KEY", "test-key")
    monkeypatch.setattr(OrsClient, "_last_live_at", None)
    answers = [_too_many(), _too_many(), FakeResponse({"ok": 1})]
    client = _client(tmp_path, lambda *a, **k: answers.pop(0))
    client.request("directions", PATH, {"coordinates": [[3, 3], [4, 4]]})
    assert client.live["directions"] == 3


def test_retries_stop_at_the_daily_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("ORS_API_KEY", "test-key")
    monkeypatch.setattr(OrsClient, "_last_live_at", None)
    calls = []

    def post(*a, **k):
        calls.append(1)
        return _too_many()

    client = _client(tmp_path, post)
    client.limits["directions"] = 2
    with pytest.raises(RuntimeError, match="free-plan limit"):
        client.request("directions", PATH, {"coordinates": [[5, 5], [6, 6]]})
    assert len(calls) == 2


def test_refused_retry_is_cached_and_reported_as_unroutable(cfg, hub, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(OrsClient, "_last_live_at", None)
    con = db.connect(hub)
    con.execute(INSERT, ["STN-001"])
    con.execute(INSERT, ["STN-002"])
    con.close()
    fake = RefusesStation()
    first = routing.warm_detour_leg_cache(cfg, db_path=hub, client=_client(tmp_path, fake))
    assert [r[1] for r in first["unroutable"]] == ["STN-001"] and not first["failed"]
    assert len(fake.calls) == 3  # one good leg, the refusal, and the refused wider retry
    again = RefusesStation()
    second = routing.warm_detour_leg_cache(cfg, db_path=hub, client=_client(tmp_path, again))
    assert again.calls == [] and len(second["unroutable"]) == 1 and not second["failed"]


def test_refresh_prunes_when_only_unroutable_legs_remain(cfg, hub, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(OrsClient, "_last_live_at", None)
    cache = tmp_path / "ors"
    cache.mkdir()
    stale = cache / "directions_deadbeefdeadbeef.json"
    stale.write_text("{}", encoding="utf-8")
    result = routing.refresh_cache(cfg, prune=True, db_path=hub, cache_dir=cache, client=_client(tmp_path, RefusesStation()))
    assert result["unroutable"] >= 1 and result["failed"] == 0
    assert not stale.exists() and result["pruned"] == 1

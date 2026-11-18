"""reachable_points: batching, order, null means False, cache reads spend no call. All offline, no live calls."""

from __future__ import annotations

from fleetfuel import config, routing
from fleetfuel.routing.client import OrsClient

BAD_LON = -1.0  # the fake finds no road for a point with this longitude


class FakeResp:
    status_code = 200
    text = ""
    headers: dict = {}

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeSnap:
    """Snaps every point except those at BAD_LON; `short` drops the last answer."""

    def __init__(self, short=False):
        self.calls = []
        self.short = short

    def __call__(self, url, json, headers, timeout):
        self.calls.append((url, json))
        locs = [None if lon == BAD_LON else {"location": [lon, lat], "snapped_distance": 5.0} for lon, lat in json["locations"]]
        return FakeResp({"locations": locs[:-1] if self.short else locs})


def _client(tmp_path, post, batch=None):
    cfg = config.load_assumptions()
    if batch:
        cfg["routing"]["snap_batch_size"] = batch
    cfg["routing"]["ors_pause_s"] = 0
    return cfg, OrsClient(cfg, tmp_path / "ors", post=post, sleep=lambda s: None)


def test_order_and_unreachable_false(tmp_path, monkeypatch):
    monkeypatch.setenv("ORS_API_KEY", "test-key")
    post = FakeSnap()
    cfg, c = _client(tmp_path, post)
    pts = [(32.0, -110.0), (32.1, BAD_LON), (32.2, -110.2)]
    assert routing.reachable_points(cfg, pts, client=c) == [True, False, True]
    url, body = post.calls[0]
    assert url.endswith("/snap/driving-hgv/json")
    assert body["radius"] == config.get(cfg, "routing.reachable_radius_m")
    assert body["locations"][0] == [-110.0, 32.0]


def test_batching_keeps_order(tmp_path, monkeypatch):
    monkeypatch.setenv("ORS_API_KEY", "test-key")
    post = FakeSnap()
    cfg, c = _client(tmp_path, post, batch=2)
    pts = [(32.0 + i / 10, BAD_LON if i in (1, 4) else -110.0 - i) for i in range(5)]
    assert routing.reachable_points(cfg, pts, client=c) == [True, False, True, True, False]
    assert len(post.calls) == 3 and c.live["snap"] == 3


def test_missing_results_are_false(tmp_path, monkeypatch):
    monkeypatch.setenv("ORS_API_KEY", "test-key")
    cfg, c = _client(tmp_path, FakeSnap(short=True))
    assert routing.reachable_points(cfg, [(32.0, -110.0), (32.1, -110.1)], client=c) == [True, False]


def test_cached_read_spends_no_call(tmp_path, monkeypatch):
    monkeypatch.setenv("ORS_API_KEY", "test-key")
    pts = [(32.0, -110.0), (32.1, BAD_LON)]
    cfg, c = _client(tmp_path, FakeSnap())
    routing.reachable_points(cfg, pts, client=c)
    post = FakeSnap()
    cfg, c2 = _client(tmp_path, post)
    assert routing.reachable_points(cfg, pts, client=c2) == [True, False]
    assert post.calls == [] and c2.live["snap"] == 0 and c2.cached["snap"] == 1


def test_empty_input(tmp_path):
    cfg, c = _client(tmp_path, FakeSnap())
    assert routing.reachable_points(cfg, [], client=c) == []

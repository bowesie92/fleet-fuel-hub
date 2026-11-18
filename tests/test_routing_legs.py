"""Detour leg paths (contract 11.6): the fuel-stop leg drawn on real roads, with a fake openrouteservice."""

from __future__ import annotations

import pytest

from fleetfuel import config, db, routing
from fleetfuel.routing.client import OrsClient
from test_routing_run import STOPS, FakeOrs, cfg, hub  # noqa: F401  (fixtures reused)


def _client(cfg, tmp_path, fake=None):
    return OrsClient(cfg, tmp_path / "ors", post=fake or FakeOrs(), sleep=lambda s: None)


def test_leg_has_three_waypoints_and_is_cached(cfg, hub, tmp_path):
    fake = FakeOrs()
    client = _client(cfg, tmp_path, fake)
    path = routing.detour_leg_path(cfg, "TUC-R01", "STN-001", 1, db_path=hub, client=client)
    assert path["type"] == "LineString"
    assert path["coordinates"] == [[-111.0, 32.2], [-111.01, 32.21], [-111.0, 32.3]]
    again = routing.detour_leg_path(cfg, "TUC-R01", "STN-001", 1, db_path=hub, client=client)
    assert again == path and len(fake.calls) == 1


def test_leg_unknown_inputs_raise_keyerror(cfg, hub, tmp_path):
    client = _client(cfg, tmp_path)
    for args in [("NOPE", "STN-001", 1), ("TUC-R01", "STN-999", 1), ("TUC-R01", "STN-001", 99),
                 ("TUC-R01", "STN-001", STOPS[-1]["seq"])]:
        with pytest.raises(KeyError):
            routing.detour_leg_path(cfg, *args, db_path=hub, client=client)


def test_leg_cache_miss_without_key_is_plain_english(cfg, hub, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "load_env", lambda: None)
    monkeypatch.delenv("ORS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ORS_API_KEY"):
        routing.detour_leg_path(cfg, "TUC-R01", "STN-001", 1, db_path=hub, client=_client(cfg, tmp_path))


def _fill_ref_detour(hub, rows):
    con = db.connect(hub)
    for r in rows:
        con.execute("INSERT INTO ref_detour (route_id, station_id, insert_after_stop, extra_miles, extra_minutes) VALUES (?,?,?,1,1)", r)
    con.close()


def test_warm_counts_live_then_cached(cfg, hub, tmp_path):
    _fill_ref_detour(hub, [("TUC-R01", "STN-001", 1), ("TUC-R01", "STN-002", 2), ("TUC-R02", "STN-001", 1)])
    fake = FakeOrs()
    first = routing.warm_detour_leg_cache(cfg, db_path=hub, client=_client(cfg, tmp_path, fake))
    assert first["rows"] == 3 and first["live"] == 2 and first["cached"] == 1  # R02 repeats R01 and not first["failed"]
    second = routing.warm_detour_leg_cache(cfg, db_path=hub, client=_client(cfg, tmp_path, fake))
    assert second["live"] == 0 and second["cached"] == 3 and len(fake.calls) == 2


def test_warm_stops_before_daily_limit(cfg, hub, tmp_path):
    _fill_ref_detour(hub, [("TUC-R01", "STN-001", 1), ("TUC-R01", "STN-002", 2), ("TUC-R01", "STN-002", 1)])
    client = _client(cfg, tmp_path)
    client.limits["directions"] = 2
    out = routing.warm_detour_leg_cache(cfg, db_path=hub, client=client)
    assert out["live"] == 2 and out["stopped_early"]


def test_run_never_warms_leg_cache(cfg, hub, tmp_path):
    fake = FakeOrs()
    routing.run(cfg, db_path=hub, cache_dir=tmp_path / "ors", client=_client(cfg, tmp_path, fake))
    legs = [b for u, b in fake.calls if "/directions/" in u and len(b["coordinates"]) == 3]
    assert not legs

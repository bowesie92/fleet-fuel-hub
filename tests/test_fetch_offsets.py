"""Offline tests for the customer point move (contract 15.1): unseeded, drawn once, never replayable."""

from __future__ import annotations

import json
import random

import pytest

from fleetfuel import config
from fleetfuel.fetch import osm

SETTINGS = {"offset_min_m": 300.0, "offset_max_m": 600.0, "offset_clear_of_any_original_m": 50.0,
            "min_candidates": 1, "coord_decimals": 5, "max_points": 40}


def originals(n: int = 60) -> list[list[float]]:
    """A fixed spread of fake 'real' points around Tucson, some only 100 m apart."""
    rng = random.Random(3)  # fixture layout only; not used for the move itself
    pts = [[round(32.15 + rng.uniform(0, 0.1), 5), round(-110.95 + rng.uniform(0, 0.1), 5)] for _ in range(n)]
    pts += [[round(p[0] + 0.0009, 5), p[1]] for p in pts[:10]]
    return sorted(pts)


def draw():
    """One full draw: the sample of originals, then their moved versions."""
    orig = originals()
    chosen = osm.sample_points(orig, SETTINGS["max_points"])
    return orig, chosen, osm.move_customer_points(chosen, orig, SETTINGS)


def write_cache(tmp_path):
    """Draw once into a temp cache and CSV, under the configured candidate rule; returns (cfg, raw_dir, cache_dir)."""
    cfg = config.load_assumptions()
    cache_dir, raw = tmp_path / "osm", tmp_path / "raw"
    settings = {**DENSE, "min_candidates": osm.offset_settings(cfg)["min_candidates"]}
    osm.write_moved_customers(dense_originals(), settings, cache_dir / "customers_points.json", raw,
                              lambda pts: [True] * len(pts), 3)
    return cfg, raw, cache_dir


def test_two_draws_from_the_same_originals_differ():
    assert draw()[2] != draw()[2]


def test_every_point_is_300_to_600_m_from_its_own_original():
    _, chosen, moved = draw()
    for a, b in zip(chosen, moved):
        assert 300 <= osm.ground_distance_m(tuple(a), tuple(b)) <= 600


def test_every_point_is_300_to_600_m_from_some_original_and_50_m_from_all():
    orig, _, moved = draw()
    for m in moved:
        dists = [osm.ground_distance_m(tuple(m), tuple(o)) for o in orig]
        assert min(dists) >= 50
        assert any(300 <= d <= 600 for d in dists)


def test_cust_ids_run_from_one_in_order():
    _, chosen, moved = draw()
    assert len(moved) == len(chosen)
    rows = osm.render_customers(moved, 5).splitlines()[1:]
    assert [r.split(",")[0] for r in rows] == [f"CUST-{n:04d}" for n in range(1, len(chosen) + 1)]


def test_cache_records_only_distance_rules_and_randomness(tmp_path):
    _, _, cache_dir = write_cache(tmp_path)
    stored = json.loads((cache_dir / "customers_points.json").read_text())
    assert set(stored) == {"offset_applied", "randomness", "settings", "points"}
    assert stored["settings"] == {"min_m": 300.0, "max_m": 600.0, "clear_m": 50.0, "min_candidates": 3}
    assert stored["randomness"] == "unseeded, drawn once"
    assert "seed" not in json.dumps(stored).lower().replace("unseeded", "")


def test_cache_holds_no_original(tmp_path):
    _, raw, cache_dir = write_cache(tmp_path)
    points = json.loads((cache_dir / "customers_points.json").read_text())["points"]
    csv_text = (raw / "customer_points.csv").read_text()
    for lat, lon in dense_originals():
        assert [lat, lon] not in points
        assert f"{lat:.5f},{lon:.5f}" not in csv_text


def test_cache_read_is_deterministic_and_never_moves_again(tmp_path):
    cfg, raw, cache_dir = write_cache(tmp_path)
    first = ((cache_dir / "customers_points.json").read_bytes(), (raw / "customer_points.csv").read_bytes())
    for _ in range(2):
        osm.fetch_customers(cfg, None, refresh=False, raw_dir=raw, cache_dir=cache_dir)
        assert first == ((cache_dir / "customers_points.json").read_bytes(), (raw / "customer_points.csv").read_bytes())


def test_cache_from_different_settings_is_refused(tmp_path):
    cfg = config.load_assumptions()
    cache_dir = tmp_path / "osm"
    cache_dir.mkdir()
    (cache_dir / "customers_points.json").write_text(json.dumps({"offset_applied": True, "settings": {}, "points": []}))
    with pytest.raises(RuntimeError, match="different settings"):
        osm.fetch_customers(cfg, None, refresh=False, raw_dir=tmp_path / "raw", cache_dir=cache_dir)


def fake_reach(bad_first: int):
    """A fake truck check: the first `bad_first` points of the first call are unreachable; later calls say yes."""
    calls: list[list] = []

    def check(points):
        calls.append([list(p) for p in points])
        return [not (len(calls) == 1 and i < bad_first) for i in range(len(points))]
    return check, calls


def test_only_unreachable_points_are_redrawn_under_the_same_rules():
    orig = originals()
    chosen = osm.sample_points(orig, SETTINGS["max_points"])
    first = osm.move_customer_points(chosen, orig, SETTINGS)
    check, calls = fake_reach(2)
    final = osm.redraw_unreachable(chosen, first, orig, SETTINGS, check, 5)
    assert len(calls) == 2 and len(calls[1]) == len(first)
    assert final[2:] == first[2:] and final[:2] != first[:2]
    for a, b in zip(chosen[:2], final[:2]):
        assert 300 <= osm.ground_distance_m(tuple(a), tuple(b)) <= 600
        assert min(osm.ground_distance_m(tuple(b), tuple(o)) for o in orig) >= 50


def test_loop_stops_with_an_error_after_max_rounds():
    orig = originals()
    chosen = osm.sample_points(orig, SETTINGS["max_points"])
    first = osm.move_customer_points(chosen, orig, SETTINGS)
    calls = []

    def never(points):
        calls.append(1)
        return [False] + [True] * (len(points) - 1)
    with pytest.raises(RuntimeError, match="not reachable by truck after 3 redraw rounds"):
        osm.redraw_unreachable(chosen, first, orig, SETTINGS, never, 3)
    assert len(calls) == 4


def test_all_reachable_needs_one_check_and_changes_nothing():
    orig = originals()
    chosen = osm.sample_points(orig, SETTINGS["max_points"])
    first = osm.move_customer_points(chosen, orig, SETTINGS)
    assert osm.redraw_unreachable(chosen, first, orig, SETTINGS, lambda p: [True] * len(p), 3) == first


# ---- at least 3 candidate businesses in the ring (P1, Cass, Nov 18)

DENSE = {**SETTINGS, "min_candidates": 3, "max_points": 20}


def dense_originals() -> list[list[float]]:
    """A 9 x 9 block of fake businesses about 220 m apart, plus one lone business 20 km away."""
    step = 0.002
    pts = [[round(32.20 + i * step, 5), round(-110.95 + j * step, 5)] for i in range(9) for j in range(9)]
    return pts + [[32.38, -110.75]]


def ring(point, orig):
    """Originals 300 to 600 m from `point`."""
    return [o for o in orig if 300 <= osm.ground_distance_m(tuple(point), tuple(o)) <= 600]


def test_every_new_point_has_at_least_three_candidates():
    orig = dense_originals()
    chosen, moved = osm.choose_and_move(orig, DENSE)
    assert len(moved) == DENSE["max_points"]
    for base, m in zip(chosen, moved):
        assert len(ring(m, orig)) >= 3 and base in ring(m, orig)
        assert min(osm.ground_distance_m(tuple(m), tuple(o)) for o in orig) >= 50


def test_a_lone_business_is_never_used():
    orig = dense_originals()
    lone = orig[-1]
    full = {**DENSE, "max_points": len(orig)}
    chosen, moved = osm.choose_and_move(orig, full)
    assert lone not in chosen and len(moved) == len(orig) - 1
    with pytest.raises(RuntimeError, match="could not place"):
        osm.move_customer_points([lone], orig, DENSE)


def test_only_thin_points_are_redrawn_and_then_pass():
    orig = dense_originals()
    _, good = osm.choose_and_move(orig, DENSE)
    thin = [[32.381, -110.75], [32.383, -110.75]]  # 100 to 350 m from the lone business: at most one candidate
    points, changed, bases = osm.redraw_thin_points(good + thin, orig, DENSE)
    n = len(good)
    assert changed == [n, n + 1] and len(bases) == 2
    assert points[:n] == good
    for i in changed:
        assert len(ring(points[i], orig)) >= 3 and 300 <= osm.ground_distance_m(tuple(points[i]), tuple(bases[i - n])) <= 600

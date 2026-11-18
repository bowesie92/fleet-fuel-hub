"""Detour arithmetic: cheapest insertion gap, unique stop points and matrix chunking (no network)."""

from __future__ import annotations

import pytest

from fleetfuel.routing import detours

M = detours.METERS_PER_MILE


def test_unique_points_merges_repeated_yard():
    stops = [{"lat": 1.0, "lon": 2.0}, {"lat": 3.0, "lon": 4.0}, {"lat": 1.0, "lon": 2.0}]
    points, order = detours.unique_points(stops)
    assert points == [(1.0, 2.0), (3.0, 4.0)]
    assert order == [0, 1, 0]


def test_chunk_size_respects_cell_limit():
    assert detours.chunk_size(13, 3500) == 59 - 13
    assert (13 + detours.chunk_size(13, 3500)) ** 2 <= 3500
    with pytest.raises(ValueError):
        detours.chunk_size(60, 3500)


def test_best_insertion_picks_cheapest_gap():
    # Points on a line at 0, 10, 20 miles (stops 0,1,2).
    # The station is 1 mile off the road, beside the 15 mile mark.
    pos = [0, 10, 20, 15]
    meters = [[abs(a - b) * M for b in pos] for a in pos]
    seconds = [[abs(a - b) * 60.0 for b in pos] for a in pos]
    for k in range(3):
        meters[k][3] += M
        meters[3][k] += M
        seconds[k][3] += 60.0
        seconds[3][k] += 60.0
    gap, miles, minutes = detours.best_insertion([0, 1, 2], 3, meters, seconds)
    assert gap == 1
    assert miles == pytest.approx(2.0)
    assert minutes == pytest.approx(2.0)


def test_best_insertion_skips_unroutable_and_floors_negative():
    meters = [[0, 5, 1], [5, 0, 1], [1, 1, 0]]
    seconds = [[0, 60, 60], [60, 0, 60], [60, 60, 0]]
    meters[0][2] = None
    assert detours.best_insertion([0, 1], 2, meters, seconds) is None
    meters = [[0, 100, 10], [100, 0, 10], [10, 10, 0]]
    seconds = [[0, 100, 10], [100, 0, 10], [10, 10, 0]]
    found = detours.best_insertion([0, 1], 2, [[float(v) if v is not None else None for v in r] for r in meters], seconds)
    assert found == (0, 0.0, 0.0)


def test_haversine_known_distance():
    # One degree of latitude is about 69 miles.
    assert detours.haversine_mi(32.0, -111.0, 33.0, -111.0) == pytest.approx(69.1, abs=0.3)

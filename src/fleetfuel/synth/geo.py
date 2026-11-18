"""synth/geo.py: small distance helpers for straight-line geography.

Business terms: how far apart two places are as the crow flies. The generator multiplies
these by the road-circuity assumption until real routing arrives in Wave 2.
"""

from __future__ import annotations

import numpy as np

EARTH_RADIUS_MI = 3958.8  # Fact: mean Earth radius in miles
MILES_PER_DEGREE_LAT = EARTH_RADIUS_MI * np.pi / 180.0
METERS_PER_DEGREE = 111_320.0  # Fact: meters in one degree of latitude


def haversine_mi(lat1, lon1, lat2, lon2):
    """Great-circle miles between points. Accepts numbers or numpy arrays."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    d_phi = p2 - p1
    d_lam = np.radians(lon2) - np.radians(lon1)
    a = np.sin(d_phi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(d_lam / 2) ** 2
    return 2 * EARTH_RADIUS_MI * np.arcsin(np.sqrt(a))


def segment_distance_mi(plat, plon, alat, alon, blat, blon):
    """Miles from point(s) P to the straight segment A-B (flat-earth math, fine at city scale)."""
    k = np.cos(np.radians((alat + blat) / 2.0))
    px, py = (plon - alon) * k, plat - alat
    bx, by = (blon - alon) * k, blat - alat
    denom = bx * bx + by * by
    t = 0.0 if denom == 0 else np.clip((px * bx + py * by) / denom, 0.0, 1.0)
    return np.hypot(px - t * bx, py - t * by) * MILES_PER_DEGREE_LAT

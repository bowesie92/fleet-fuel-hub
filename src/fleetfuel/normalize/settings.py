"""Reads normalize thresholds from config/assumptions.yaml (`pipeline.*`) through fleetfuel.config.

A missing key raises a clear error naming it; nothing is defaulted in code.
"""

from __future__ import annotations

from typing import Any

from fleetfuel import config


def setting(cfg: dict, key: str) -> Any:
    """Return an assumption by dotted key; raises KeyError naming the key if it is missing."""
    return config.get(cfg, key)

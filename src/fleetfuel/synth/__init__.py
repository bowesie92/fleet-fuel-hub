"""Generate the synthetic fleet, routes, 12 months of operations and the messy source files.

Owner: synth-builder. Entry point: `fleetfuel.synth.run(cfg)` (docs/contracts.md section 8).
"""

from __future__ import annotations

from .generator import run

__all__ = ["run"]

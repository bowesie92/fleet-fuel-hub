"""Export: write the small JSON and GeoJSON files the static site reads.

Step 5 of `uv run fleetfuel build` (docs/contracts.md section 8). The work is in
`to_site.py`; this module only exposes the stage entry point.
"""

from __future__ import annotations

from fleetfuel.export.to_site import run

__all__ = ["run"]

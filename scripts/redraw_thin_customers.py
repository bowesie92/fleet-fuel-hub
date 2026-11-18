"""One-time fix (decision P1, Cass, Nov 18): redraw every customer point with fewer than 3 candidate businesses.

A moved point is only anonymous if several original businesses lie 300 to 600 m from it: then someone who
reruns the public OpenStreetMap query cannot tell which one it came from. This script reads the current
moved points from the cache, finds the ones with fewer than `fetch.customers.min_candidate_originals`
candidates, redraws only those (unseeded, under all the usual rules, including the truck-reachability
check), and rewrites the cache and data/raw/customer_points.csv. Every other point stays exactly as it is.

The originals come from git history of this checkout (--originals-rev, a commit from before the move), are
held in memory only, and nothing about the draw is stored. Prints counts only, never a coordinate.

Usage:
    uv run python scripts/redraw_thin_customers.py --originals-rev 9406634
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
from fleetfuel import config  # noqa: E402
from fleetfuel.fetch import osm  # noqa: E402

_spec = importlib.util.spec_from_file_location("check_customer_offsets", REPO_ROOT / "scripts" / "check_customer_offsets.py")
offsets = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(offsets)


def main(argv: list[str] | None = None) -> int:
    """Redraw the thin points, check they are reachable by truck, and save."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--originals-rev", required=True, help="a commit of this checkout from before the move")
    args = parser.parse_args(argv)

    cfg = config.load_assumptions()
    settings = osm.offset_settings(cfg)
    _, found = offsets.originals_at(args.originals_rev)
    originals = sorted({(lat, lon) for lat, lon in found.values()})
    originals = [list(p) for p in originals]

    cache_file = REPO_ROOT / "data" / "cache" / "osm" / osm.CUSTOMERS_CACHE
    stored = json.loads(cache_file.read_text(encoding="utf-8"))
    points = stored["points"]

    points, changed, bases = osm.redraw_thin_points(points, originals, settings)
    print(f"thin points redrawn: {len(changed)} of {len(points)}")
    if changed:
        rounds = int(config.get(cfg, "fetch.customers.max_reach_rounds"))
        fixed = osm.redraw_unreachable(bases, [points[i] for i in changed], originals, settings,
                                       osm.reachable_by_truck(cfg), rounds)
        for i, new in zip(changed, fixed):
            points[i] = new
    osm.save_customers(points, settings, cache_file, REPO_ROOT / "data" / "raw")
    print("saved the cache and customer_points.csv; run the offset check next")
    return 0


if __name__ == "__main__":
    sys.exit(main())

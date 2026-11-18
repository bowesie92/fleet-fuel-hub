"""Check that no original customer coordinate survives anywhere, and that every customer point was moved.

The stand-in customer stops started as real Tucson food-service locations (OpenStreetMap). Before the repo
goes public each point is moved 300 to 600 m (config: fetch.customers.offset_*), so no stop sits on a real
business. The sample, its order and the offsets are drawn from unseeded randomness and stored nowhere (contract
15.1), so which original a CUST ID came from is unknown, even to this script. It proves:

1. Moved: the target's data/raw/customer_points.csv has the same CUST IDs as before, every point lies between
   the configured minimum and maximum distance from at least one original, and no point is closer than
   fetch.customers.offset_clear_of_any_original_m to any original, and at least
   fetch.customers.min_candidate_originals originals lie in each point's ring (so nobody can tell which
   business a point came from).
2. Gone: no coordinate pair in any scanned text lies within --radius-m (default 5 m, which covers rounding of
   a stored point) of an original point. A pair is two neighbouring numbers that read as (lat, lon) or (lon, lat)
   inside the Tucson area. At wider radii (10 to 25 m) a few hits are expected and harmless: road lines from
   openrouteservice and synthetic GPS pings that pass the same street as an original business.
   A data/raw/telematics.csv ping logged at driving speed (fetch.customers.road_ping_min_mph or more) is a
   point on a road, so one within the radius is reported on its own line, not as a failure, unless there
   are more than fetch.customers.road_ping_max_exempt in one text or one lies within the clear distance of
   a customer stop. Slower and parked pings always fail.

The originals are read from a git revision of THIS checkout (--originals-rev, a commit from before the
offset): all 995 points of that revision's OpenStreetMap customer cache plus its customer_points.csv, never
from a file outside git. With --history the target's every file version, commit message and
author is scanned (uses check_repo_rules.history_texts); otherwise the files git would commit, plus
site/data (which the build regenerates).

Usage:
    uv run python scripts/check_customer_offsets.py --originals-rev 9406634
    uv run python scripts/check_customer_offsets.py --originals-rev 9406634 --history --repo PATH
Exit code 0 means clean, 1 means a problem was found. Prints counts and paths only, never a coordinate.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import json
import math
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CUSTOMERS_REL = "data/raw/customer_points.csv"
CACHE_REL = "data/cache/osm/customers_points.json"
METERS_PER_DEG_LAT = 111_320.0
GRID_DEG = 0.001  # grid cell for the nearest-original lookup, about 100 m
NUMBER = re.compile(r"-?\d{1,3}\.\d{3,}")
LAT_RANGE, LON_RANGE = (31.0, 34.0), (-113.0, -110.0)

_spec = importlib.util.spec_from_file_location("check_repo_rules", REPO_ROOT / "scripts" / "check_repo_rules.py")
rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rules)
sys.path.insert(0, str(REPO_ROOT / "src"))
from fleetfuel import config  # noqa: E402


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Ground distance in meters between two (lat, lon) points (equirectangular, fine at these distances)."""
    mean_lat = math.radians((a[0] + b[0]) / 2)
    dy = (a[0] - b[0]) * METERS_PER_DEG_LAT
    dx = (a[1] - b[1]) * METERS_PER_DEG_LAT * math.cos(mean_lat)
    return math.hypot(dx, dy)


def read_points(text: str) -> dict[str, tuple[float, float]]:
    """customer_points.csv text as {customer_id: (lat, lon)}."""
    return {r["customer_id"]: (float(r["lat"]), float(r["lon"])) for r in csv.DictReader(io.StringIO(text))}


def git_show(rev: str, rel: str) -> str:
    """One file's text at a git revision of this checkout."""
    return subprocess.run(["git", "show", f"{rev}:{rel}"], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout


def originals_at(rev: str) -> tuple[set[str], dict[str, tuple[float, float]]]:
    """(the CUST IDs, every original point) at a git revision: the CSV's sampled points plus all cached points."""
    sampled = read_points(git_show(rev, CUSTOMERS_REL))
    cached = json.loads(git_show(rev, CACHE_REL))
    cached = cached["points"] if isinstance(cached, dict) else cached
    points = dict(sampled)
    points.update({f"cache-{i}": (float(lat), float(lon)) for i, (lat, lon) in enumerate(cached)})
    return set(sampled), points


class OriginalIndex:
    """A grid of the original points, so 'is this pair near an original?' is a quick lookup."""

    def __init__(self, points: dict[str, tuple[float, float]]):
        self.grid: dict[tuple[int, int], list[tuple[float, float]]] = {}
        for p in set(points.values()):  # the sampled CSV points are also in the cache: count each place once
            self.grid.setdefault(self.cell(p), []).append(p)

    @staticmethod
    def cell(p: tuple[float, float]) -> tuple[int, int]:
        return int(math.floor(p[0] / GRID_DEG)), int(math.floor(p[1] / GRID_DEG))

    def distances(self, p: tuple[float, float], radius_m: float) -> list[float]:
        """Distances to every original within `radius_m` of `p`."""
        ci, cj = self.cell(p)
        reach = int(math.ceil(radius_m / (GRID_DEG * METERS_PER_DEG_LAT * 0.8))) + 1
        found = []
        for i in range(ci - reach, ci + reach + 1):
            for j in range(cj - reach, cj + reach + 1):
                found += [d for q in self.grid.get((i, j), ()) if (d := distance_m(p, q)) <= radius_m]
        return found

    def near(self, p: tuple[float, float], radius_m: float) -> bool:
        ci, cj = self.cell(p)
        reach = int(math.ceil(radius_m / (GRID_DEG * METERS_PER_DEG_LAT * 0.8))) + 1
        for i in range(ci - reach, ci + reach + 1):
            for j in range(cj - reach, cj + reach + 1):
                for q in self.grid.get((i, j), ()):
                    if distance_m(p, q) <= radius_m:
                        return True
        return False


def in_tucson(lat: float, lon: float) -> bool:
    """True when (lat, lon) falls in the wide Tucson-area box the checks look at."""
    return LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]


def json_pairs(node) -> list[tuple[float, float]]:
    """Every real coordinate in parsed JSON: two-number arrays ([lon, lat] or [lat, lon]) and objects with lat and lon."""
    pairs: list[tuple[float, float]] = []
    stack = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            if len(item) in (2, 3) and all(isinstance(v, (int, float)) for v in item[:2]):
                a, b = float(item[0]), float(item[1])
                pairs += [(lat, lon) for lat, lon in ((a, b), (b, a)) if in_tucson(lat, lon)]
            else:
                stack.extend(item)
        elif isinstance(item, dict):
            lat, lon = item.get("lat"), item.get("lon")
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and in_tucson(float(lat), float(lon)):
                pairs.append((float(lat), float(lon)))
            stack.extend(item.values())
    return pairs


TELEMATICS_REL = "data/raw/telematics.csv"  # the only file whose moving pings may be set apart


def moving_rows(text: str, min_mph: float) -> set[int]:
    """Line numbers of telematics rows logged at driving speed (`speed_mph` at or above `min_mph`).

    A GPS ping at speed is a point on a road, not a place, so one that passes within a few meters of a
    business fronting that road is reported on its own line instead of failing the check. A slower or
    parked ping (a truck at a stop) still fails like any other coordinate. Only data/raw/telematics.csv
    is treated this way, and main() still fails on too many such pings or on one near a customer stop.
    """
    lines = text.splitlines()
    if not lines or "speed_mph" not in lines[0].split(","):
        return set()
    col = lines[0].split(",").index("speed_mph")
    moving = set()
    for n, line in enumerate(lines[1:], start=1):
        cells = line.split(",")
        try:
            if float(cells[col]) >= min_mph:
                moving.add(n)
        except (IndexError, ValueError):
            continue
    return moving


def coordinate_pairs(text: str, name: str = "", skip_lines: set[int] | None = None) -> list[tuple[float, float]]:
    """Neighbouring numbers that read as a Tucson-area (lat, lon), in either order.

    In a CSV a pair never spans two rows (one row's lon next to the next row's lat is not a place);
    in other files it may span lines, because indented GeoJSON puts each coordinate on its own line.
    CSV rows whose line number is in `skip_lines` are left out.
    """
    path = name.split(" @")[0]
    if path.endswith((".json", ".geojson")):
        try:
            return json_pairs(json.loads(text))
        except ValueError:
            pass  # not valid JSON after all; fall back to the text scan below
    chunks = text.splitlines() if path.endswith(".csv") else [text]
    skip = skip_lines or set()
    pairs = []
    for n, chunk in enumerate(chunks):
        if n in skip:
            continue
        nums = [float(x) for x in NUMBER.findall(chunk)]
        for a, b in zip(nums, nums[1:]):
            for lat, lon in ((a, b), (b, a)):
                if LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]:
                    pairs.append((lat, lon))
    return pairs


def current_texts(repo: Path) -> dict[str, str]:
    """The files git would commit in `repo`, plus everything under site/data (build output)."""
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=repo,
                         capture_output=True, text=True, check=True).stdout.split()
    names = set(out) | {p.relative_to(repo).as_posix() for p in (repo / "site" / "data").glob("*") if p.is_file()}
    texts = {}
    for name in sorted(names):
        if name in rules.NEVER_SCAN or not (repo / name).is_file():
            continue
        try:
            texts[name] = (repo / name).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
    return texts


def check_moved(repo: Path, ids: set[str], index: "OriginalIndex", low: float, high: float, clear: float,
                min_candidates: int = 1) -> int:
    """Same CUST IDs; each point has at least `min_candidates` originals `low` to `high` m away and none closer
    than `clear`. Returns the number of problems."""
    moved = read_points((repo / CUSTOMERS_REL).read_text(encoding="utf-8"))
    problems = 0
    if set(moved) != ids:
        print(f"  CUST IDs differ: {len(ids - set(moved))} missing, {len(set(moved) - ids)} new")
        problems += 1
    nearest, unplaced, thin, fewest = [], 0, 0, None
    for point in moved.values():
        near = index.distances(point, high + 0.5)
        nearest.append(min(near) if near else float("inf"))
        in_ring = sum(1 for d in near if low <= d <= high)
        fewest = in_ring if fewest is None else min(fewest, in_ring)
        if not any(low - 0.5 <= d <= high + 0.5 for d in near):
            unplaced += 1
        if in_ring < min_candidates:
            thin += 1
    too_close = sum(1 for d in nearest if d < clear)
    print(f"moved: {len(moved)} points; closest any point gets to any original {min(nearest):.0f} m (must be {clear:.0f}+), "
          f"{too_close} too close; {unplaced} with no original {low:.0f} to {high:.0f} m away; "
          f"fewest candidates in a ring {fewest} (must be {min_candidates}+), {thin} below that")
    return problems + too_close + unplaced + thin


def main(argv: list[str] | None = None) -> int:
    """Run both checks and print a coordinate-free report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--originals-rev", required=True, help="a commit of this checkout from before the offset")
    parser.add_argument("--repo", type=Path, default=None, help="scan this other clone instead of this checkout")
    parser.add_argument("--history", action="store_true", help="scan every file version, commit message and author")
    parser.add_argument("--radius-m", type=float, default=5.0, help="how close to an original counts as a hit (5 m covers coordinate rounding)")
    args = parser.parse_args(argv)

    repo = (args.repo or REPO_ROOT).resolve()
    cfg = config.load_assumptions()
    ids, originals = originals_at(args.originals_rev)
    index = OriginalIndex(originals)
    low = float(config.get(cfg, "fetch.customers.offset_min_m"))
    high = float(config.get(cfg, "fetch.customers.offset_max_m"))
    clear = float(config.get(cfg, "fetch.customers.offset_clear_of_any_original_m"))
    min_candidates = int(config.get(cfg, "fetch.customers.min_candidate_originals"))
    min_mph = float(config.get(cfg, "fetch.customers.road_ping_min_mph"))
    max_road = int(config.get(cfg, "fetch.customers.road_ping_max_exempt"))
    stops = OriginalIndex(read_points((repo / CUSTOMERS_REL).read_text(encoding="utf-8"))) if (repo / CUSTOMERS_REL).is_file() else None
    problems = check_moved(repo, ids, index, low, high, clear, min_candidates) if (repo / CUSTOMERS_REL).is_file() else 0

    texts = rules.history_texts(repo) if args.history else current_texts(repo)
    hits, road, road_at_stop = {}, {}, 0
    for name, text in texts.items():
        moving = moving_rows(text, min_mph) if name.split(" @")[0] == TELEMATICS_REL else set()
        n = sum(1 for p in coordinate_pairs(text, name, skip_lines=moving) if index.near(p, args.radius_m))
        if n:
            hits[name] = n
        if moving:
            lines = text.splitlines()
            near = [p for i in moving for p in coordinate_pairs(lines[i], "row.csv") if index.near(p, args.radius_m)]
            if near:
                road[name] = len(near)
                road_at_stop += sum(1 for p in near if stops is not None and stops.near(p, clear))
    print(f"original coordinates: {len(set(originals.values()))} places, {len(texts)} texts scanned, radius {args.radius_m:.0f} m, "
          f"{sum(hits.values())} hits in {len(hits)} texts")
    for name, n in sorted(hits.items())[:40]:
        print(f"  {name}: {n}")
    print(f"GPS pings logged at {min_mph:g} mph or more that pass within {args.radius_m:.0f} m of an original (points on a road, "
          f"reported, not a failure up to {max_road} per text and none within {clear:.0f} m of a customer stop): "
          f"{sum(road.values())} in {len(road)} texts, {road_at_stop} near a stop")
    for name, n in sorted(road.items())[:40]:
        print(f"  {name}: {n}")
    problems += len(hits) + road_at_stop + sum(1 for n in road.values() if n > max_road)
    print("RESULT:", "CLEAN" if problems == 0 else "PROBLEMS FOUND")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

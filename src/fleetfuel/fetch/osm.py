"""OpenStreetMap inputs: truck-capable fuel stations (contract 4.2) and anonymous customer points (4.3).

In business terms:
- Stations: one Overpass query lists every fuel station between Phoenix, Casa Grande and Tucson.
  We keep the ones a tractor-trailer can use: tagged for heavy trucks, or run by a known
  truck-stop brand (because OSM truck tags are incomplete). A station mapped twice (a point and
  a building outline) is counted once.
- Customers: one query lists Tucson restaurants, fast food and cafes. We keep ONLY their
  coordinates, as stand-in delivery stops. Names, addresses, OSM IDs and tags are dropped in
  memory before anything touches the disk, including the cache.
"""

from __future__ import annotations

import importlib.util
import json
import math
import random
import re
import secrets
from pathlib import Path
from typing import Any

import requests

from fleetfuel import config
from fleetfuel.fetch import cache

STATIONS_CACHE = "stations_overpass.json"
CUSTOMERS_CACHE = "customers_points.json"
EARTH_RADIUS_M = 6_371_000.0  # Fact: mean Earth radius, for distances between two coordinates


# ---------------------------------------------------------------- Overpass access

def _bbox_text(bbox: list[float]) -> str:
    """Overpass wants south,west,north,east."""
    south, west, north, east = bbox
    return f"{south},{west},{north},{east}"


def stations_query(cfg: dict) -> str:
    """The Overpass query for every fuel station in the stations bounding box, with centers for ways."""
    bbox = _bbox_text(config.get(cfg, "fetch.overpass.stations_bbox"))
    timeout = config.get(cfg, "fetch.overpass.timeout_s")
    return f'[out:json][timeout:{timeout}];nwr["amenity"="fuel"]({bbox});out center tags;'


def customers_query(cfg: dict) -> str:
    """The Overpass query for food-service places in the Tucson bounding box (centers for ways)."""
    bbox = _bbox_text(config.get(cfg, "fetch.customers.bbox"))
    timeout = config.get(cfg, "fetch.overpass.timeout_s")
    parts = []
    for tag in config.get(cfg, "fetch.customers.osm_tags"):
        key, _, value = tag.partition("=")
        parts.append(f'nwr["{key}"="{value}"]({bbox});')
    return f"[out:json][timeout:{timeout}];({''.join(parts)});out center;"


def run_overpass(cfg: dict, query: str) -> dict:
    """Send one query to Overpass with the polite User-Agent, retrying politely when it is busy."""
    endpoint = config.get(cfg, "fetch.overpass.endpoint")
    headers = {"User-Agent": config.get(cfg, "fetch.overpass.user_agent")}
    # Overpass may legitimately work for its whole query timeout before answering.
    timeout = cache.timeouts(cfg, min_read_s=float(config.get(cfg, "fetch.overpass.timeout_s")))
    max_retries, wait_s = cache.retry_settings(cfg)
    for attempt in range(max_retries + 1):
        resp = cache.send_with_backoff(
            lambda: requests.post(endpoint, data={"data": query}, headers=headers, timeout=timeout),
            max_retries=max_retries, wait_s=wait_s, label="Overpass",
        )
        body = resp.json()
        # Overpass can answer 200 with a runtime error remark and no elements. Treat that as busy.
        if "elements" in body and "timed out" not in str(body.get("remark", "")).lower():
            return body
        if attempt < max_retries:
            wait = wait_s * (2**attempt)
            print(f"[fetch] Overpass returned a runtime error remark; waiting {wait:.0f}s before retry")
            cache.pause(wait)
    raise RuntimeError("Overpass kept returning a runtime error; try again later.")


class OverpassSession:
    """Runs Overpass queries one at a time, pausing between them (rate-limit courtesy)."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.calls = 0

    def query(self, query: str) -> dict:
        if self.calls:
            cache.pause(float(config.get(self.cfg, "fetch.overpass.pause_between_queries_s")))
        self.calls += 1
        return run_overpass(self.cfg, query)


# ---------------------------------------------------------------- geometry helpers

def element_point(element: dict) -> tuple[float, float] | None:
    """Latitude and longitude of an OSM element: the point itself, or the center of a way or relation."""
    if "lat" in element and "lon" in element:
        return float(element["lat"]), float(element["lon"])
    center = element.get("center")
    if center and "lat" in center and "lon" in center:
        return float(center["lat"]), float(center["lon"])
    return None


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Straight-line distance in meters between two lat/lon points (haversine)."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


# ---------------------------------------------------------------- stations

# Rules in order of strength; the first one that applies is the station's `access_basis`.
# chain_locator is only ever set by an `add` override (contract 4.11), never by this module.
BASIS_ORDER = ["truck_tag", "truck_brand", "cardlock", "fleet_access", "chain_locator", "truck_evidence", "diesel_only"]
CONFIRMED_BASES = frozenset(BASIS_ORDER[:5])  # truck_evidence and diesel_only are only "unverified"
TRUCK_HGV_VALUES = ("yes", "designated")  # Fact (contract 4.2): hgv values that mean trucks are allowed


def station_rules(cfg: dict) -> dict:
    """Gather the station filter settings from config/assumptions.yaml in one place."""
    g = lambda k: config.get(cfg, f"fetch.overpass.{k}")  # noqa: E731
    return {
        "brand_aliases": g("truck_brand_aliases"), "cardlock_aliases": g("cardlock_aliases"),
        "fleet_access_values": g("fleet_access_values"), "evidence_hgv_values": g("truck_evidence_hgv_values"),
        "evidence_words": g("truck_evidence_words"), "tucson_bbox": g("tucson_metro_bbox"),
    }


def in_bbox(point: tuple[float, float], bbox: list[float]) -> bool:
    """True when a (lat, lon) point lies inside a south, west, north, east box."""
    south, west, north, east = bbox
    return south <= point[0] <= north and west <= point[1] <= east


def is_truck_tagged(tags: dict) -> bool:
    """True when OSM says heavy trucks can fuel here (hgv=yes or designated, or fuel:HGV_diesel=yes)."""
    return tags.get("hgv") in TRUCK_HGV_VALUES or tags.get("fuel:HGV_diesel") == "yes"


def _plain(text: str) -> str:
    """Lower-case a label and straighten curly apostrophes so Love's matches however it is typed."""
    return text.replace("’", "'").replace("‘", "'").lower()


def has_word(texts: list[str], words: list[str]) -> bool:
    """True when any word or phrase appears as a whole word in any text (case-insensitive).

    Whole-word matching stops the short alias 'TA' from matching every name with 'ta' inside it.
    """
    haystacks = [_plain(t) for t in texts if t]
    for word in words:
        pattern = r"(?<![a-z0-9])" + re.escape(_plain(word)) + r"(?![a-z0-9])"
        if any(re.search(pattern, h) for h in haystacks):
            return True
    return False


def matches_truck_brand(tags: dict, aliases: list[str]) -> bool:
    """True when the brand or name contains a truck-stop brand as a whole word."""
    return has_word([tags.get("brand"), tags.get("name")], aliases)


def access_basis(tags: dict, point: tuple[float, float], rules: dict) -> str | None:
    """The strongest reason this fuel station is kept, or None when it is not truck-capable.

    Outside the Tucson metro box only truck tags and truck brands count. Inside it, cardlock
    or fleet sites also count, and so does weaker evidence (marked unverified).
    """
    if is_truck_tagged(tags):
        return "truck_tag"
    if matches_truck_brand(tags, rules["brand_aliases"]):
        return "truck_brand"
    if not in_bbox(point, rules["tucson_bbox"]):
        return None
    if has_word([tags.get("brand"), tags.get("name"), tags.get("operator")], rules["cardlock_aliases"]):
        return "cardlock"
    diesel = tags.get("fuel:diesel") == "yes"
    if diesel and tags.get("access") in rules["fleet_access_values"]:
        return "fleet_access"
    if diesel and (tags.get("hgv") in rules["evidence_hgv_values"]
                   or has_word([tags.get("name"), tags.get("operator")], rules["evidence_words"])):
        return "truck_evidence"
    if diesel:
        return "diesel_only"  # contract 4.2 (v1.5): any other Tucson-box diesel station, unverified
    return None


def load_forbidden_terms() -> list[str]:
    """The private forbidden-terms list (CLAUDE.md rule 2), or [] where it does not exist (e.g. CI).

    Reuses scripts/check_repo_rules.py. The terms stay in memory and are never printed or stored.
    """
    script = config.REPO_ROOT / "scripts" / "check_repo_rules.py"
    if not script.is_file():
        return []
    spec = importlib.util.spec_from_file_location("check_repo_rules", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load_terms(module.find_terms_file())


def has_forbidden_term(tags: dict, terms: list[str]) -> bool:
    """True when any tag (name, brand, operator or otherwise) contains a forbidden term."""
    if not terms:
        return False
    text = json.dumps(tags, ensure_ascii=False).lower()
    return any(t in text for t in terms)


def _norm_key(tags: dict) -> str:
    """Brand (or, without a brand, name) reduced to letters and digits, for spotting a double mapping."""
    label = tags.get("brand") or tags.get("name") or ""
    return re.sub(r"[^a-z0-9]", "", _plain(label))


def screen_stations(response: dict, rules: dict, terms: list[str]) -> tuple[list[dict], list[str]]:
    """Truck-capable stations from a raw Overpass response, plus the osm_refs dropped for a name conflict.

    Stations with a forbidden term in any tag are dropped before they become candidates, so they
    never get an ID. Only their osm_ref is returned, never the matching text.
    """
    out, conflicts = [], []
    for el in response.get("elements", []):
        point = element_point(el)
        tags = el.get("tags") or {}
        if point is None:
            continue
        basis = access_basis(tags, point, rules)
        if basis is None:
            continue
        ref = f"{el['type']}/{el['id']}"
        if has_forbidden_term(tags, terms):
            conflicts.append(ref)
            continue
        out.append({
            "osm_ref": ref, "lat": point[0], "lon": point[1],
            "name": tags.get("name"), "brand": tags.get("brand"), "operator": tags.get("operator"),
            "city": tags.get("addr:city"),
            "truck_tagged": is_truck_tagged(tags), "truck_brand": matches_truck_brand(tags, rules["brand_aliases"]),
            "access_basis": basis,
            "_key": _norm_key(tags), "_tag_count": len(tags),
        })
    return sorted(out, key=lambda c: c["osm_ref"]), sorted(conflicts)


def prune_response(response: dict, kept_refs: set[str]) -> dict:
    """The cache copy of an Overpass response: only the stations that were kept.

    This is a deliberate narrowing of "cache the raw response" (contract section 5): fuel stations
    that are not truck-capable, or whose names conflict with CLAUDE.md rule 2, are never stored.
    """
    kept = [el for el in response.get("elements", []) if f"{el['type']}/{el['id']}" in kept_refs]
    return {**response, "elements": kept}


def _same_station(a: dict, b: dict, max_m: float) -> bool:
    """Two entries are one physical station if they are close and share a brand or name,
    or are both truck-stop brands this close together."""
    if distance_m((a["lat"], a["lon"]), (b["lat"], b["lon"])) > max_m:
        return False
    same_label = bool(a["_key"]) and a["_key"] == b["_key"]
    return same_label or (a["truck_brand"] and b["truck_brand"])


def _merge(keep: dict, other: dict) -> dict:
    """Combine two entries for one station: keep the better-tagged location, fill gaps, OR the flags,
    and keep the stronger access basis."""
    # More tags wins; on a tie the entry that sorts first by osm_ref wins.
    first, second = sorted([keep, other], key=lambda c: (-c["_tag_count"], c["osm_ref"]))
    merged = dict(first)
    for field in ("name", "brand", "operator", "city"):
        merged[field] = first[field] or second[field]
    merged["truck_tagged"] = first["truck_tagged"] or second["truck_tagged"]
    merged["truck_brand"] = first["truck_brand"] or second["truck_brand"]
    merged["access_basis"] = min(first["access_basis"], second["access_basis"], key=BASIS_ORDER.index)
    merged["_key"] = first["_key"] or second["_key"]
    merged["_tag_count"] = max(first["_tag_count"], second["_tag_count"])
    return merged


def dedupe_stations(candidates: list[dict], max_m: float) -> tuple[list[dict], int]:
    """Collapse the same physical station mapped twice. Returns (stations, number removed)."""
    kept: list[dict] = []
    removed = 0
    for cand in candidates:
        for i, existing in enumerate(kept):
            if _same_station(existing, cand, max_m):
                kept[i] = _merge(existing, cand)
                removed += 1
                break
        else:
            kept.append(cand)
    return sorted(kept, key=lambda c: c["osm_ref"]), removed


def station_class(station: dict) -> str:
    """travel_center for truck-stop brands, cardlock for fleet fueling networks, branded when a
    brand is set, else unbranded."""
    if station["truck_brand"]:
        return "travel_center"
    if station["access_basis"] == "cardlock":
        return "cardlock"
    return "branded" if station["brand"] else "unbranded"


def to_geojson(stations: list[dict]) -> dict:
    """The contract 4.2 FeatureCollection: IDs STN-001... in osm_ref order, 6-decimal coordinates."""
    features = []
    for n, s in enumerate(sorted(stations, key=lambda c: c["osm_ref"]), start=1):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(s["lon"], 6), round(s["lat"], 6)]},
            "properties": {
                "station_id": f"STN-{n:03d}", "osm_ref": s["osm_ref"], "name": s["name"], "brand": s["brand"],
                "operator": s["operator"], "city": s["city"], "truck_tagged": s["truck_tagged"],
                "truck_brand": s["truck_brand"], "station_class": station_class(s),
                "truck_access": "confirmed" if s["access_basis"] in CONFIRMED_BASES else "unverified",
                "access_basis": s["access_basis"],
            },
        })
    return {"type": "FeatureCollection", "features": features}


def render_geojson(collection: dict) -> str:
    """One feature per line so diffs and hand checks are readable."""
    lines = [json.dumps(f, ensure_ascii=False) for f in collection["features"]]
    body = ",\n".join(lines)
    return '{"type": "FeatureCollection", "features": [\n' + body + "\n]}\n"


def fetch_stations(cfg: dict, session: OverpassSession, *, refresh: bool, raw_dir: Path, cache_dir: Path,
                   terms: list[str] | None = None) -> dict:
    """Get the Overpass response (cache first), screen and dedupe it, and write stations.geojson.

    `terms` defaults to the private forbidden-terms list. Stations matching it are dropped
    before IDs are assigned and never reach the cache; only their osm_refs are returned.
    """
    cache_file = cache_dir / STATIONS_CACHE
    rules = station_rules(cfg)
    terms = load_forbidden_terms() if terms is None else terms
    response = None if refresh else cache.read_json(cache_file)
    fresh = response is None
    if fresh:
        response = session.query(stations_query(cfg))
    candidates, conflicts = screen_stations(response, rules, terms)
    pruned = prune_response(response, {c["osm_ref"] for c in candidates})
    if fresh or pruned != response:
        cache.write_json(cache_file, pruned, indent=None)
    stations, removed = dedupe_stations(candidates, float(config.get(cfg, "fetch.overpass.dedupe_distance_m")))
    cache.write_text(raw_dir / "stations.geojson", render_geojson(to_geojson(stations)))
    return {"stations": len(stations), "deduped": removed, "candidates": len(candidates),
            "name_conflicts": conflicts}


# ---------------------------------------------------------------- customers

def sanitize_points(response: dict, decimals: int) -> list[list[float]]:
    """Reduce an Overpass response to sorted, unique [lat, lon] pairs. Everything else is discarded here."""
    pairs = set()
    for el in response.get("elements", []):
        point = element_point(el)
        if point is not None:
            pairs.add((round(point[0], decimals), round(point[1], decimals)))
    return [[lat, lon] for lat, lon in sorted(pairs)]


def sample_points(points: list[list[float]], max_points: int, rng: random.Random | None = None) -> list[list[float]]:
    """Pick at most max_points real points, in a random order, with an unseeded source (nothing replayable).

    The pick and the order (which becomes CUST order) are drawn once and never stored as a recipe.
    """
    rng = rng or secrets.SystemRandom()
    return rng.sample(points, min(max_points, len(points)))


def render_customers(points: list[list[float]], decimals: int) -> str:
    """CSV text: customer_id, lat, lon, with CUST-0001 and up and fixed decimals."""
    lines = ["customer_id,lat,lon"]
    for n, (lat, lon) in enumerate(points, start=1):
        lines.append(f"CUST-{n:04d},{lat:.{decimals}f},{lon:.{decimals}f}")
    return "\n".join(lines) + "\n"


RANDOMNESS = "unseeded, drawn once"  # Fact: what the cache says about how the points were drawn
METERS_PER_DEG_LAT = 111_320.0  # Fact: ground meters per degree of latitude
MAX_REDRAWS = 1000  # Setting: guard so a bad configuration fails loudly instead of looping forever


def ground_distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Ground distance in meters between two (lat, lon) points (flat-earth estimate, fine at city scale)."""
    mean_lat = math.radians((a[0] + b[0]) / 2)
    dy = (a[0] - b[0]) * METERS_PER_DEG_LAT
    dx = (a[1] - b[1]) * METERS_PER_DEG_LAT * math.cos(mean_lat)
    return math.hypot(dx, dy)


def offset_settings(cfg: dict) -> dict:
    """The distance rules and sizes for moving points (config only; nothing random lives here)."""
    return {
        "offset_min_m": float(config.get(cfg, "fetch.customers.offset_min_m")),
        "offset_max_m": float(config.get(cfg, "fetch.customers.offset_max_m")),
        "offset_clear_of_any_original_m": float(config.get(cfg, "fetch.customers.offset_clear_of_any_original_m")),
        "min_candidates": int(config.get(cfg, "fetch.customers.min_candidate_originals")),
        "coord_decimals": int(config.get(cfg, "fetch.customers.coord_decimals")),
        "max_points": int(config.get(cfg, "fetch.customers.max_points")),
    }


def cache_rules(settings: dict) -> dict:
    """The only settings the cache records: min, max and clear distances in meters, and the candidate count."""
    return {"min_m": settings["offset_min_m"], "max_m": settings["offset_max_m"],
            "clear_m": settings["offset_clear_of_any_original_m"], "min_candidates": settings["min_candidates"]}


def _near_any(point: tuple[float, float], grid: dict, clear_m: float) -> bool:
    """True when `point` is within `clear_m` of any original point (grid lookup of neighbouring cells)."""
    cell = clear_m / METERS_PER_DEG_LAT * 2
    cx, cy = int(point[0] / cell), int(point[1] / cell)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for other in grid.get((cx + dx, cy + dy), ()):
                if ground_distance_m(point, other) < clear_m:
                    return True
    return False


class Originals:
    """The original points, held in memory only, with two quick lookups: 'is anything this close?' (the
    clear distance) and 'which lie in the 300 to 600 m ring?' (the candidates an onlooker could not tell apart)."""

    def __init__(self, originals: list[list[float]], settings: dict):
        self.points = [(float(lat), float(lon)) for lat, lon in originals]
        self.lo, self.hi = settings["offset_min_m"], settings["offset_max_m"]
        self.clear = settings["offset_clear_of_any_original_m"]
        self.clear_cell = self.clear / METERS_PER_DEG_LAT * 2
        self.ring_cell = self.hi / METERS_PER_DEG_LAT
        self.clear_grid: dict = {}
        self.ring_grid: dict = {}
        for pt in self.points:
            self.clear_grid.setdefault((int(pt[0] / self.clear_cell), int(pt[1] / self.clear_cell)), []).append(pt)
            self.ring_grid.setdefault((int(pt[0] / self.ring_cell), int(pt[1] / self.ring_cell)), []).append(pt)

    def too_close(self, point: tuple[float, float]) -> bool:
        """True when `point` is within the clear distance of any original."""
        return _near_any(point, self.clear_grid, self.clear)

    def in_ring(self, point: tuple[float, float]) -> list[tuple[float, float]]:
        """Every original 300 to 600 m from `point`. A longitude degree is shorter on the ground than a
        latitude degree, so the lookup reaches two grid cells each way."""
        cx, cy = int(point[0] / self.ring_cell), int(point[1] / self.ring_cell)
        found = []
        for dx in (-2, -1, 0, 1, 2):
            for dy in (-2, -1, 0, 1, 2):
                found += [o for o in self.ring_grid.get((cx + dx, cy + dy), ())
                          if self.lo <= ground_distance_m(point, o) <= self.hi]
        return found


def place_point(base: list[float], index: Originals, settings: dict, rng: random.Random) -> list[float] | None:
    """One stand-in point 300 to 600 m from `base` in a random direction, or None when no draw passes the rules.

    A draw is thrown away when, after rounding, it falls outside the distance range, lands within the clear
    distance of ANY original (so it never sits on another real business), or has fewer than
    `min_candidates` originals in its 300 to 600 m ring (so nobody can tell which business it came from).
    """
    dec, lo, hi = settings["coord_decimals"], settings["offset_min_m"], settings["offset_max_m"]
    lat, lon = base
    for _ in range(MAX_REDRAWS):
        dist, angle = rng.uniform(lo, hi), rng.uniform(0, 2 * math.pi)
        new_lat = lat + dist * math.cos(angle) / METERS_PER_DEG_LAT
        new_lon = lon + dist * math.sin(angle) / (METERS_PER_DEG_LAT * math.cos(math.radians(lat)))
        cand = (round(new_lat, dec), round(new_lon, dec))
        if (lo <= ground_distance_m((lat, lon), cand) <= hi and not index.too_close(cand)
                and len(index.in_ring(cand)) >= settings["min_candidates"]):
            return [cand[0], cand[1]]
    return None


def move_customer_points(chosen: list[list[float]], originals: list[list[float]], settings: dict,
                         rng: random.Random | None = None) -> list[list[float]]:
    """Move every stand-in stop 300 to 600 m in a random direction, so no stop sits on a real business.

    Every draw comes from an unseeded source (secrets.SystemRandom), so the move cannot be replayed.
    Points are handled in CUST order under the rules in `place_point`. Stops with an error if a point
    cannot be placed (`choose_and_move` skips originals in places too sparse to hide in).
    """
    index, rng = Originals(originals, settings), rng or secrets.SystemRandom()
    moved = []
    for base in chosen:
        point = place_point(base, index, settings, rng)
        if point is None:
            raise RuntimeError("could not place a customer point after many draws; check the offset settings")
        moved.append(point)
    return moved


def choose_and_move(originals: list[list[float]], settings: dict,
                    rng: random.Random | None = None) -> tuple[list[list[float]], list[list[float]]]:
    """Pick originals in an unseeded random order and move each one, skipping any that sit too far from
    other businesses to pass the candidate rule, until `max_points` are placed. Returns (chosen, moved),
    both in memory only; the order becomes the CUST order."""
    index, rng = Originals(originals, settings), rng or secrets.SystemRandom()
    chosen, moved = [], []
    for base in rng.sample(originals, len(originals)):
        if len(moved) == settings["max_points"]:
            break
        point = place_point(base, index, settings, rng)
        if point is not None:
            chosen.append(base)
            moved.append(point)
    return chosen, moved


def redraw_thin_points(points: list[list[float]], originals: list[list[float]], settings: dict,
                       rng: random.Random | None = None) -> tuple[list[list[float]], list[int], list[list[float]]]:
    """Redraw every existing point with fewer than `min_candidates` originals 300 to 600 m away.

    Which original a point came from is recorded nowhere, so the new point is drawn around one of the
    point's current candidates, picked at random, or, where no candidate leaves room to hide, around another
    original picked at random. Returns (all points, indexes that changed, the originals they were drawn
    around); the last two stay in memory only, for the truck-reachability redraw.
    """
    index, rng = Originals(originals, settings), rng or secrets.SystemRandom()
    points = [list(p) for p in points]
    changed, bases = [], []
    for i, point in enumerate(points):
        near = index.in_ring((point[0], point[1]))
        if len(near) >= settings["min_candidates"]:
            continue
        others = [o for o in index.points if o not in near]
        for base in rng.sample(near, len(near)) + rng.sample(others, len(others)):
            new = place_point(list(base), index, settings, rng)
            if new is not None:
                points[i] = new
                changed.append(i)
                bases.append(list(base))
                break
        else:
            raise RuntimeError("could not place a customer point anywhere under the candidate rule; check the settings")
    return points, changed, bases


def _cache_payload(points: list[list[float]], settings: dict) -> dict:
    """What the customer cache holds: moved points, the three distance rules, and how they were drawn."""
    return {"offset_applied": True, "randomness": RANDOMNESS, "settings": cache_rules(settings), "points": points}


def fetch_customers(cfg: dict, session: OverpassSession, *, refresh: bool, raw_dir: Path, cache_dir: Path) -> dict:
    """Get anonymous, moved customer coordinates (cache first) and write customer_points.csv.

    A cache read never redraws: the cached moved points are the only source of truth and the files come
    out byte-identical. A cache miss, or `refresh=True`, queries Overpass, draws a NEW unseeded sample
    and new offsets (so `fetch --refresh` changes the points), moves them before anything is written,
    and stores moved points only. No original coordinate, seed, offset or source index is ever stored.
    """
    settings = offset_settings(cfg)
    cache_file = cache_dir / CUSTOMERS_CACHE
    stored = None if refresh else cache.read_json(cache_file)
    if stored is not None:
        if not isinstance(stored, dict) or stored.get("settings") != cache_rules(settings):
            raise RuntimeError("customer cache was built with different settings; run fetch with refresh to rebuild it")
        points = stored["points"]
        cache.write_text(raw_dir / "customer_points.csv", render_customers(points, settings["coord_decimals"]))
        return {"available": len(points), "written": len(points)}
    originals = sanitize_points(session.query(customers_query(cfg)), settings["coord_decimals"])  # raw response never stored
    rounds = int(config.get(cfg, "fetch.customers.max_reach_rounds"))
    return write_moved_customers(originals, settings, cache_file, raw_dir, reachable_by_truck(cfg), rounds)


def reachable_by_truck(cfg: dict):
    """The live check used when drawing: asks openrouteservice (snap service, cached) which points a truck can reach."""
    from fleetfuel import routing  # imported here so offline cache reads never load the routing client

    return lambda points: routing.reachable_points(cfg, [tuple(p) for p in points])


def redraw_unreachable(chosen: list[list[float]], points: list[list[float]], originals: list[list[float]],
                       settings: dict, reachable, max_rounds: int) -> list[list[float]]:
    """Check every point can be reached by truck; redraw only the ones that cannot, each from its own original
    with a fresh unseeded offset under the same rules. Stops with an error after `max_rounds` redraw rounds.
    Nothing about the draw is kept."""
    points = [list(p) for p in points]
    for _ in range(max_rounds + 1):
        flags = reachable(points)
        bad = [i for i, ok in enumerate(flags) if not ok]
        if not bad:
            return points
        if _ == max_rounds:
            break
        fresh = move_customer_points([chosen[i] for i in bad], originals, settings)
        for i, new in zip(bad, fresh):
            points[i] = new
    raise RuntimeError(f"{len(bad)} customer points are still not reachable by truck after {max_rounds} redraw rounds; "
                       "check the routing service or raise fetch.customers.max_reach_rounds")


def save_customers(points: list[list[float]], settings: dict, cache_file: Path, raw_dir: Path) -> None:
    """Write the moved points: the cache (the only source of truth) and data/raw/customer_points.csv."""
    cache.write_json(cache_file, _cache_payload(points, settings), indent=None)
    cache.write_text(raw_dir / "customer_points.csv", render_customers(points, settings["coord_decimals"]))


def write_moved_customers(originals: list[list[float]], settings: dict, cache_file: Path, raw_dir: Path,
                          reachable, max_rounds: int) -> dict:
    """Draw the sample and offsets once from `originals` (held in memory only), redraw any point a truck
    cannot reach, then write cache plus CSV."""
    chosen, points = choose_and_move(originals, settings)
    points = redraw_unreachable(chosen, points, originals, settings, reachable, max_rounds)
    save_customers(points, settings, cache_file, raw_dir)
    return {"available": len(originals), "written": len(points)}

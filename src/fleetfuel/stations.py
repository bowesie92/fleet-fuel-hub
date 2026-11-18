"""The station list every other step reads: stations.geojson with the manual overrides applied.

In business terms: `data/raw/stations.geojson` is what OpenStreetMap says. People then fix a few
entries by hand in `config/station_overrides.yaml` (drop a truck scale, merge a site that was
mapped twice, rename a station). This module applies those fixes, matching on `osm_ref`
because station IDs renumber whenever the station filter changes (docs/contracts.md 4.11).
Synth, the pipeline, analytics and export must read stations only through `load_stations`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from fleetfuel import config
from fleetfuel.fetch.osm import BASIS_ORDER, distance_m, has_word, matches_truck_brand, station_class

OVERRIDES_PATH = config.REPO_ROOT / "config" / "station_overrides.yaml"
ACTIONS = ("exclude", "merge_into", "rename", "add")
ENTRY_FIELDS = {"osm_ref", "station_id", "action", "target_osm_ref", "new_name", "reason",
                "add_id", "name", "brand", "city", "lat", "lon", "source_url"}
ADD_REQUIRED = ("add_id", "name", "lat", "lon", "source_url", "reason")
ADD_ID = re.compile(r"^A\d{2,}$")
OSM_REF = re.compile(r"^(node|way|relation)/\d+$")
FILL_FIELDS = ("name", "brand", "operator", "city")
FRAME_EXTRAS = ["lat", "lon", "merged_osm_refs", "source_url", "add_id"]
PROPERTY_COLUMNS = [
    "station_id", "osm_ref", "name", "brand", "operator", "city",
    "truck_tagged", "truck_brand", "station_class", "truck_access", "access_basis",
]


# ---------------------------------------------------------------- overrides file

def _check_entry(n: int, entry: Any) -> dict:
    """Validate one override entry and return it with every field present."""
    where = f"station_overrides.yaml entry {n}"
    if not isinstance(entry, dict):
        raise ValueError(f"{where} must be a mapping of fields.")
    unknown = set(entry) - ENTRY_FIELDS
    if unknown:
        raise ValueError(f"{where} has unknown fields: {sorted(unknown)}.")
    if entry.get("action") == "add":
        return _check_add(where, entry)
    ref = entry.get("osm_ref")
    if not isinstance(ref, str) or not OSM_REF.match(ref):
        raise ValueError(f"{where} needs an osm_ref like 'node/123', 'way/456' or 'relation/789'.")
    action = entry.get("action")
    if action not in ACTIONS:
        raise ValueError(f"{where} ({ref}) has action {action!r}; use one of {list(ACTIONS)}.")
    if not str(entry.get("reason") or "").strip():
        raise ValueError(f"{where} ({ref}) needs a one-line reason.")
    target = entry.get("target_osm_ref")
    if action == "merge_into":
        if not isinstance(target, str) or not OSM_REF.match(target):
            raise ValueError(f"{where} ({ref}) merge_into needs a valid target_osm_ref.")
        if target == ref:
            raise ValueError(f"{where} ({ref}) cannot merge a station into itself.")
    if action == "rename" and not str(entry.get("new_name") or "").strip():
        raise ValueError(f"{where} ({ref}) rename needs new_name.")
    return {
        "osm_ref": ref, "station_id": entry.get("station_id"), "action": action,
        "target_osm_ref": target if action == "merge_into" else None,
        "new_name": str(entry["new_name"]).strip() if action == "rename" else None,
        "reason": str(entry["reason"]).strip(),
    }


def _check_add(where: str, entry: dict) -> dict:
    """Validate an `add` entry (a site confirmed from a chain's public locator) and fill every field."""
    missing = [f for f in ADD_REQUIRED if _blank(entry.get(f)) or (isinstance(entry.get(f), str) and not entry[f].strip())]
    if missing:
        raise ValueError(f"{where} (add) is missing required fields: {missing}.")
    if not ADD_ID.match(str(entry["add_id"])):
        raise ValueError(f"{where} add_id must look like 'A01'.")
    try:
        lat, lon = float(entry["lat"]), float(entry["lon"])
    except (TypeError, ValueError):
        raise ValueError(f"{where} (add) lat and lon must be numbers.") from None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"{where} (add) lat or lon is out of range.")
    if not str(entry["source_url"]).startswith(("http://", "https://")):
        raise ValueError(f"{where} (add) source_url must be an http(s) link.")
    clean = lambda k: str(entry[k]).strip() if not _blank(entry.get(k)) else None  # noqa: E731
    return {
        "osm_ref": None, "station_id": entry.get("station_id"), "action": "add", "target_osm_ref": None,
        "new_name": None, "reason": str(entry["reason"]).strip(), "add_id": clean("add_id"),
        "name": clean("name"), "brand": clean("brand"), "city": clean("city"),
        "lat": round(lat, 6), "lon": round(lon, 6), "source_url": clean("source_url"),
    }


def load_overrides(path: Path | None = None) -> list[dict]:
    """Read and validate config/station_overrides.yaml. A missing file or empty list means no overrides."""
    path = Path(path or OVERRIDES_PATH)
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("station_overrides.yaml must have a top-level 'overrides' list.")
    entries = data.get("overrides") or []
    if not isinstance(entries, list):
        raise ValueError("station_overrides.yaml 'overrides' must be a list.")
    checked = [_check_entry(n, e) for n, e in enumerate(entries, start=1)]
    refs = [e["osm_ref"] for e in checked if e["osm_ref"]]
    dupes = sorted({r for r in refs if refs.count(r) > 1})
    if dupes:
        raise ValueError(f"station_overrides.yaml lists the same osm_ref more than once: {dupes}.")
    add_ids = [e["add_id"] for e in checked if e["action"] == "add"]
    if len(set(add_ids)) != len(add_ids):
        raise ValueError("station_overrides.yaml lists the same add_id more than once.")
    return checked


# ---------------------------------------------------------------- stations

def _blank(value: Any) -> bool:
    """True for None, NaN and empty text."""
    return value is None or (isinstance(value, float) and pd.isna(value)) or value == ""


def read_geojson(raw_dir: Path | None = None) -> pd.DataFrame:
    """stations.geojson as a frame: every 4.2 property plus lat and lon, sorted by station_id."""
    path = Path(raw_dir or config.RAW_DIR) / "stations.geojson"
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found. Run `uv run fleetfuel fetch` first.")
    features = json.loads(path.read_text(encoding="utf-8"))["features"]
    rows = []
    for f in features:
        lon, lat = f["geometry"]["coordinates"]
        rows.append({**f["properties"], "lat": lat, "lon": lon})
    frame = pd.DataFrame(rows, columns=[*PROPERTY_COLUMNS, "lat", "lon"])
    frame = frame.astype({c: object for c in ("name", "brand", "operator", "city")})
    return frame.sort_values("station_id").reset_index(drop=True)


def _merge_rows(target: dict, source: dict) -> dict:
    """Fold a duplicate station into the one that survives (contract 4.11 merge_into)."""
    merged = dict(target)
    for field in FILL_FIELDS:
        if _blank(merged[field]) and not _blank(source[field]):
            merged[field] = source[field]
    merged["truck_tagged"] = bool(target["truck_tagged"] or source["truck_tagged"])
    merged["truck_brand"] = bool(target["truck_brand"] or source["truck_brand"])
    merged["access_basis"] = min(target["access_basis"], source["access_basis"], key=BASIS_ORDER.index)
    confirmed = "confirmed" in (target["truck_access"], source["truck_access"])
    merged["truck_access"] = "confirmed" if confirmed else "unverified"
    merged["station_class"] = station_class(merged)
    merged["merged_osm_refs"] = [*target["merged_osm_refs"], source["osm_ref"], *source["merged_osm_refs"]]
    return merged


def _record(entry: dict, status: str, station_id: str | None, target_id: str | None) -> dict:
    return {
        "osm_ref": entry["osm_ref"], "station_id": station_id or entry["station_id"], "action": entry["action"],
        "target_station_id": target_id, "reason": entry["reason"], "status": status,
    }


def _apply_one(entry: dict, rows: dict[str, dict]) -> dict:
    """Apply one override to the station rows (keyed by osm_ref) and describe what happened."""
    source = rows.get(entry["osm_ref"])
    if source is None:
        return _record(entry, "not_found", None, None)
    if entry["action"] == "exclude":
        del rows[entry["osm_ref"]]
        return _record(entry, "applied", source["station_id"], None)
    if entry["action"] == "rename":
        source["name"] = entry["new_name"]
        return _record(entry, "applied", source["station_id"], None)
    target = rows.get(entry["target_osm_ref"])
    if target is None:
        return _record(entry, "not_found", source["station_id"], None)
    rows[target["osm_ref"]] = _merge_rows(target, source)
    del rows[entry["osm_ref"]]
    return _record(entry, "applied", source["station_id"], target["station_id"])


def _nearest_within(rows: dict[str, dict], lat: float, lon: float, max_m: float) -> dict | None:
    """The existing station closest to a point and within max_m meters, or None."""
    best, best_m = None, max_m
    for row in rows.values():
        d = distance_m((lat, lon), (row["lat"], row["lon"]))
        if d <= best_m:
            best, best_m = row, d
    return best


def _added_class(row: dict, cfg: dict) -> str:
    """Class of an added station: travel_center for a truck-stop brand, cardlock when its brand or
    name is a cardlock network (fetch.overpass.cardlock_aliases), else branded or unbranded."""
    if row["truck_brand"]:
        return "travel_center"
    aliases = config.get(cfg, "fetch.overpass.cardlock_aliases")
    if has_word([row["brand"], row["name"]], aliases):
        return "cardlock"
    return "branded" if not _blank(row["brand"]) else "unbranded"


def _apply_add(entry: dict, rows: dict[str, dict], cfg: dict) -> dict:
    """Apply an `add`: confirm a station within add_match_distance_m, else create STN-<add_id> (contract 4.11)."""
    max_m = float(config.get(cfg, "stations.add_match_distance_m"))
    row = _nearest_within(rows, entry["lat"], entry["lon"], max_m)
    status = "applied_matched"
    if row is None:
        status = "applied_new"
        row = {
            "station_id": f"STN-{entry['add_id']}", "osm_ref": None, "name": None, "brand": None, "operator": None,
            "city": None, "truck_tagged": False, "truck_brand": False, "station_class": "unbranded",
            "truck_access": "confirmed", "access_basis": "chain_locator", "lat": entry["lat"], "lon": entry["lon"],
            "merged_osm_refs": [], "source_url": None, "add_id": None,
        }
        rows[f"add/{entry['add_id']}"] = row
    for field in ("name", "brand", "city"):
        if _blank(row[field]):
            row[field] = entry[field]
    row["truck_brand"] = bool(row["truck_brand"]) or matches_truck_brand(
        {"brand": entry["brand"], "name": entry["name"]}, config.get(cfg, "fetch.overpass.truck_brand_aliases"))
    row["truck_access"], row["access_basis"] = "confirmed", "chain_locator"
    row["station_class"] = _added_class(row, cfg)
    if _blank(row["add_id"]):  # when two adds land on one station, the first keeps the source columns
        row["add_id"], row["source_url"] = entry["add_id"], entry["source_url"]
    return {"osm_ref": None, "station_id": row["station_id"], "action": "add", "target_station_id": row["station_id"],
            "reason": entry["reason"], "status": status, "add_id": entry["add_id"]}


def load_stations(raw_dir: Path | None = None, overrides_path: Path | None = None,
                  cfg: dict | None = None) -> tuple[pd.DataFrame, list[dict]]:
    """stations.geojson with the manual overrides applied, plus one record per override entry.

    The frame has every contract 4.2 property plus `lat`, `lon` and `merged_osm_refs` (the
    osm_refs folded into each station, for source_ids_json). Each record is
    {osm_ref, station_id, action, target_station_id, reason, status}, where status is
    `applied` or `not_found` (an unknown osm_ref is reported, never an error). `add` entries run
    last; their status is `applied_new` or `applied_matched`, their record also carries `add_id`,
    and the frame's `source_url` and `add_id` columns say where a confirmation came from.
    """
    frame = read_geojson(raw_dir)
    overrides = load_overrides(overrides_path)
    rows = {r["osm_ref"]: {**r, "merged_osm_refs": [], "source_url": None, "add_id": None}
            for r in frame.to_dict("records")}
    first = [e for e in overrides if e["action"] != "add"]
    adds = [e for e in overrides if e["action"] == "add"]
    by_entry = {id(e): _apply_one(e, rows) for e in first}
    if adds:
        cfg = cfg or config.load_assumptions()
        by_entry.update({id(e): _apply_add(e, rows, cfg) for e in adds})
    records = [by_entry[id(e)] for e in overrides]
    out = pd.DataFrame(list(rows.values()), columns=[*PROPERTY_COLUMNS, *FRAME_EXTRAS])
    for col in ("osm_ref", "name", "brand", "operator", "city", "source_url", "add_id"):
        out[col] = out[col].astype(object).where(out[col].notna(), None)  # absent text is None, never NaN
    out = out.sort_values("station_id").reset_index(drop=True)
    return out, records


def display_name(row: Any) -> str:
    """What to call a station on screen: its name, else its brand, else 'Unnamed fuel station · STN-001'."""
    for field in ("name", "brand"):
        value = row[field]
        if not _blank(value):
            return str(value)
    return f"Unnamed fuel station · {row['station_id']}"

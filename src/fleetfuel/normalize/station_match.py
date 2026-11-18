"""Station matching: tie each card swipe to a station_id, and leave it blank when unsure.

Evidence for a (swipe, station) pair, each scored 0 to 1 and combined with the weights in
`pipeline.station_match`:

* name: fuzzy similarity of the merchant text to the station name or brand (rapidfuzz);
* brand: do the station's brand words appear in the merchant text;
* distance: how close the truck's GPS position was at swipe time (nearest pings within
  `ping_window_minutes`, plus a straight-line estimate between them).

If the truck had a GPS position, only stations within `max_distance_mi` of it are candidates.
Merchant strings that carry no readable name (codes such as "MERCH 004512") can still be
tied to a station once the same string has been seen next to the same station on several
swipes (an "alias"). Below `min_confidence`, or when two stations are nearly tied, the swipe
stays unmatched: station_id is NULL. This module never guesses and never sees ground truth.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process

from fleetfuel.normalize.settings import setting

EARTH_RADIUS_MI = 3958.8
PERCENT = 100.0
CONFIDENCE_DECIMALS = 6  # confidence is rounded so a score of exactly 0.70 is not lost to float noise
NS_UNIT = "datetime64[ns]"
SECONDS = pd.Timedelta(seconds=1)
EPOCH = pd.Timestamp("1970-01-01")

_DIGITS_WITH_HASH = re.compile(r"#\s*\d+")
MERCHANT_CODE = re.compile(r"^MERCH\s*\d+$", re.IGNORECASE)  # only "MERCH ######" texts are codes; other card text is a name
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def clean_name(text: object) -> str:
    """Lower-case a merchant or station name and drop punctuation, store numbers and apostrophes."""
    if text is None or (isinstance(text, float) and np.isnan(text)):
        return ""
    words = str(text).lower().replace("'", "").replace("&", " and ")
    words = _DIGITS_WITH_HASH.sub(" ", words)
    words = _NON_ALNUM.sub(" ", words)
    return " ".join(token for token in words.split() if not token.isdigit())


def merchant_text(name: object, city: object) -> str:
    """Cleaned merchant text with the swipe's own city words removed ("BRAND #812 TUCSON" in Tucson becomes "brand").

    The city says where the station is, not which station it is, so it only dilutes the name score.
    Falls back to the full cleaned name if removing the city would leave nothing.
    """
    cleaned = clean_name(name)
    city_words = set(clean_name(city).split())
    kept = " ".join(w for w in cleaned.split() if w not in city_words)
    return kept or cleaned


def haversine_mi(lat1, lon1, lat2, lon2):
    """Great-circle distance in miles; works on numpy arrays and broadcasts."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dlmb = np.radians(lon2) - np.radians(lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_MI * np.arcsin(np.sqrt(a))


@dataclass
class MatchResult:
    """Output of `match_stations`."""

    matches: pd.DataFrame  # one row per swipe: source_row, station_id, confidence, method
    unmatched: pd.DataFrame  # source_row, merchant_name, best_candidate_station_id, best_confidence, reason
    aliases: dict[str, dict[str, list[str]]] = field(default_factory=dict)  # station_id -> fuel_card_names, merchant_codes


def estimate_positions(txns: pd.DataFrame, pings: pd.DataFrame, window_minutes: float) -> dict[str, np.ndarray]:
    """For each swipe, find the truck's last ping before and first ping after it (within the window).

    Returns arrays aligned with `txns`: before_lat/lon, after_lat/lon and an interpolated lat/lon
    between the two (NaN where a ping is missing, for example inside a GPS gap).
    """
    window = pd.Timedelta(minutes=window_minutes)
    left = txns[["truck_id", "ts_local"]].copy()
    left["ts_local"] = left["ts_local"].astype(NS_UNIT)
    left["truck_id"] = left["truck_id"].astype(str)
    left["_pos"] = np.arange(len(left))
    left = left.sort_values("ts_local")
    right = pings[["truck_id", "ts_local", "lat", "lon"]].copy()
    right["ts_local"] = right["ts_local"].astype(NS_UNIT)
    right["truck_id"] = right["truck_id"].astype(str)
    right["ping_ts"] = right["ts_local"]
    right = right.sort_values("ts_local")

    def nearest(direction: str) -> pd.DataFrame:
        merged = pd.merge_asof(
            left, right, on="ts_local", by="truck_id", direction=direction, tolerance=window
        ).sort_values("_pos")
        return merged

    before, after = nearest("backward"), nearest("forward")
    b_lat, b_lon = before["lat"].to_numpy(dtype="float64"), before["lon"].to_numpy(dtype="float64")
    a_lat, a_lon = after["lat"].to_numpy(dtype="float64"), after["lon"].to_numpy(dtype="float64")
    def seconds(column: pd.Series) -> np.ndarray:
        return ((column - EPOCH) / SECONDS).to_numpy(dtype="float64")

    t, tb, ta = seconds(before["ts_local"]), seconds(before["ping_ts"]), seconds(after["ping_ts"])
    span = ta - tb
    with np.errstate(invalid="ignore", divide="ignore"):
        frac = np.where(span > 0, (t - tb) / span, 0.0)
    i_lat = b_lat + frac * (a_lat - b_lat)
    i_lon = b_lon + frac * (a_lon - b_lon)
    return {"b_lat": b_lat, "b_lon": b_lon, "a_lat": a_lat, "a_lon": a_lon, "i_lat": i_lat, "i_lon": i_lon}


def distance_matrix(positions: dict[str, np.ndarray], stations: pd.DataFrame) -> np.ndarray:
    """Miles from the truck's estimated position(s) to every station: shape (swipes, stations).

    A swipe's distance is the smallest of the distances from the ping before, the ping after
    and the point between them. NaN means the truck had no GPS position near that time.
    """
    s_lat = stations["lat"].to_numpy(dtype="float64")[None, :]
    s_lon = stations["lon"].to_numpy(dtype="float64")[None, :]
    best = np.full((len(positions["b_lat"]), len(stations)), np.nan)
    for lat_key, lon_key in (("b_lat", "b_lon"), ("a_lat", "a_lon"), ("i_lat", "i_lon")):
        d = haversine_mi(positions[lat_key][:, None], positions[lon_key][:, None], s_lat, s_lon)
        best = np.fmin(best, d)
    return best


def name_score_matrix(queries: list[str], stations: pd.DataFrame) -> np.ndarray:
    """Fuzzy name similarity (0 to 100) of each cleaned merchant text to each station, shape (texts, stations).

    A station is scored against both its name and its brand, and the better score counts.
    """
    names = [clean_name(n) for n in stations["name"]]
    brands = [clean_name(b) for b in stations["brand"]]
    by_name = process.cdist(queries, names, scorer=fuzz.WRatio, dtype=np.float32)
    by_brand = process.cdist(queries, brands, scorer=fuzz.WRatio, dtype=np.float32)
    return np.maximum(by_name, by_brand).astype("float64")


def brand_agreement_matrix(texts: list[str], stations: pd.DataFrame, name_score_min: float) -> np.ndarray:
    """True where every word of the station's brand appears (fuzzily) in the cleaned merchant text."""
    brand_tokens = [tuple(clean_name(b).split()) for b in stations["brand"]]
    result = np.zeros((len(texts), len(stations)), dtype=bool)
    for tokens in {t for t in brand_tokens if t}:  # one pass per distinct brand, not per station
        columns = [j for j, t in enumerate(brand_tokens) if t == tokens]
        for i, text in enumerate(texts):
            words = text.split()
            if words and all(any(fuzz.ratio(t, w) >= name_score_min for w in words) for t in tokens):
                result[i, columns] = True
    return result


def learn_aliases(
    merchant_names: pd.Series, distance: np.ndarray, max_distance_mi: float, min_swipes: int, min_share: float
) -> dict[str, tuple[int, float]]:
    """Find merchant strings that always occur next to the same station: {string: (station index, share)}.

    Uses GPS only (the station nearest the truck, within `max_distance_mi`), never the name.
    """
    has_gps = ~np.isnan(distance).all(axis=1)
    within = np.where(np.isnan(distance), np.inf, distance)
    nearest = np.where(within.min(axis=1) <= max_distance_mi, within.argmin(axis=1), -1)
    frame = pd.DataFrame({"name": merchant_names.to_numpy(), "nearest": nearest})[has_gps]
    aliases: dict[str, tuple[int, float]] = {}
    for name, group in frame.groupby("name"):
        total = len(group)
        counts = group[group["nearest"] >= 0]["nearest"].value_counts()
        if total >= min_swipes and not counts.empty and counts.iloc[0] / total >= min_share:
            aliases[str(name)] = (int(counts.index[0]), float(counts.iloc[0] / total))
    return aliases


def match_stations(txns: pd.DataFrame, stations: pd.DataFrame, pings: pd.DataFrame, cfg: dict) -> MatchResult:
    """Match swipes to stations using name, brand and GPS evidence. Needs txns: source_row, truck_id, ts_local, merchant_name."""
    p = "pipeline.station_match."
    window_min = setting(cfg, p + "ping_window_minutes")
    max_dist = float(setting(cfg, p + "max_distance_mi"))
    name_min = float(setting(cfg, p + "name_score_min"))
    w_name, w_brand, w_dist = (float(setting(cfg, p + k)) for k in ("weight_name", "weight_brand", "weight_distance"))
    min_conf = float(setting(cfg, p + "min_confidence"))
    margin = float(setting(cfg, p + "ambiguity_margin"))
    alias_swipes = int(setting(cfg, p + "alias_min_swipes"))
    alias_share = float(setting(cfg, p + "alias_min_share"))
    weight_total = w_name + w_brand + w_dist

    txns = txns.reset_index(drop=True)
    stations = stations.reset_index(drop=True)
    n, m = len(txns), len(stations)
    if n == 0 or m == 0:
        return _all_unmatched(txns, "no stations to match against" if m == 0 else "no swipes")

    positions = estimate_positions(txns, pings, window_min)
    distance = distance_matrix(positions, stations)
    has_position = ~np.isnan(distance).all(axis=1)

    cities = txns["merchant_city"] if "merchant_city" in txns else pd.Series([None] * n)
    texts = np.array([merchant_text(a, b) for a, b in zip(txns["merchant_name"], cities)], dtype=object)
    unique_texts, inverse = np.unique(texts, return_inverse=True)
    unique_list = [str(u) for u in unique_texts]
    name_score = name_score_matrix(unique_list, stations)[inverse]
    name_ev = np.where(name_score >= name_min, name_score / PERCENT, 0.0)
    brand_ev = brand_agreement_matrix(unique_list, stations, name_min)[inverse].astype("float64")
    with np.errstate(invalid="ignore"):
        dist_ev = np.where(np.isnan(distance), 0.0, np.clip(1 - distance / max_dist, 0.0, 1.0))

    aliases = learn_aliases(txns["merchant_name"], distance, max_dist, alias_swipes, alias_share)
    alias_ev = np.zeros((n, m))
    for i, name in enumerate(txns["merchant_name"].to_numpy()):
        if name in aliases:
            j, share = aliases[name]
            alias_ev[i, j] = share
    alias_used = (name_ev == 0) & (alias_ev > 0)
    name_ev = np.where(alias_used, alias_ev, name_ev)

    confidence = np.round((w_name * name_ev + w_brand * brand_ev + w_dist * dist_ev) / weight_total, CONFIDENCE_DECIMALS)
    with np.errstate(invalid="ignore"):
        eligible = ~has_position[:, None] | (distance <= max_dist)
    scored = np.where(eligible, confidence, -1.0)

    return _decide(
        txns, stations, scored, confidence, eligible, name_ev, brand_ev, dist_ev, alias_used, distance,
        min_conf=min_conf, margin=margin, max_dist=max_dist,
    )


def miles_text(value: float) -> str:
    """A distance with one decimal and the right plural: "1.0 mile", "1.4 miles"."""
    rounded = round(float(value), 1)
    return f"{rounded:.1f} {'mile' if rounded == 1.0 else 'miles'}"


def _decide(txns, stations, scored, confidence, eligible, name_ev, brand_ev, dist_ev, alias_used, distance, *, min_conf, margin, max_dist) -> MatchResult:
    """Pick the best eligible station per swipe, applying the minimum-confidence and ambiguity rules."""
    n = len(txns)
    rows = np.arange(n)
    best = scored.argmax(axis=1)
    best_conf = scored[rows, best]
    runner_up = scored.copy()
    runner_up[rows, best] = -1.0
    second_conf = runner_up.max(axis=1)
    no_candidate = best_conf < 0

    # For reporting only: when nothing was eligible, the candidate shown is the station closest to the truck.
    closest = np.where(np.isnan(distance), np.inf, distance).argmin(axis=1)
    report_best = np.where(no_candidate, closest, best)
    report_conf = confidence[rows, report_best]

    low = best_conf < min_conf
    ambiguous = ~no_candidate & ~low & (np.round(best_conf - second_conf, CONFIDENCE_DECIMALS) < margin) & (second_conf >= 0)
    accepted = ~no_candidate & ~low & ~ambiguous

    ids = stations["station_id"].to_numpy(dtype=object)
    method: list[str] = []
    for i in range(n):
        j = best[i]
        parts = []
        if name_ev[i, j] > 0:
            parts.append("code" if alias_used[i, j] else "name")
        if brand_ev[i, j] > 0:
            parts.append("brand")
        if dist_ev[i, j] > 0:
            parts.append("gps")
        method.append("gps_only" if parts == ["gps"] else "+".join(parts) or "none")

    matches = pd.DataFrame(
        {
            "source_row": txns["source_row"].to_numpy(),
            "station_id": np.where(accepted, ids[best], None),
            "confidence": np.where(accepted, best_conf, np.nan),
            "method": np.where(accepted, np.array(method, dtype=object), "unmatched"),
        }
    )

    reasons = []
    for i in range(n):
        if accepted[i]:
            reasons.append("")
        elif no_candidate[i]:
            reasons.append(
                f"closest candidate {ids[closest[i]]} was {miles_text(distance[i, closest[i]])} from the truck's GPS position, "
                f"beyond the {miles_text(max_dist)} limit"
            )
        elif low[i]:
            reasons.append(f"best confidence {best_conf[i]:.2f} is below the minimum {min_conf:.2f}")
        else:
            other = ids[runner_up[i].argmax()]
            reasons.append(f"ambiguous: {ids[best[i]]} ({best_conf[i]:.2f}) and {other} ({second_conf[i]:.2f}) are too close to call")
    unmatched = pd.DataFrame(
        {
            "source_row": txns["source_row"].to_numpy(),
            "merchant_name": txns["merchant_name"].to_numpy(),
            "best_candidate_station_id": ids[report_best],
            "best_confidence": report_conf,
            "reason": reasons,
        }
    )[~accepted].reset_index(drop=True)

    alias_map: dict[str, dict[str, set[str]]] = {}
    for i in np.flatnonzero(accepted):
        entry = alias_map.setdefault(str(ids[best[i]]), {"fuel_card_names": set(), "merchant_codes": set()})
        text = str(txns["merchant_name"].iloc[i])
        entry["merchant_codes" if MERCHANT_CODE.match(text) else "fuel_card_names"].add(text)
    aliases = {sid: {k: sorted(v) for k, v in d.items()} for sid, d in alias_map.items()}
    return MatchResult(matches=matches, unmatched=unmatched, aliases=aliases)


def _all_unmatched(txns: pd.DataFrame, reason: str) -> MatchResult:
    """Result for the degenerate case where there is nothing to match."""
    matches = pd.DataFrame(
        {
            "source_row": txns["source_row"].to_numpy(),
            "station_id": None,
            "confidence": np.nan,
            "method": "unmatched",
        }
    )
    unmatched = pd.DataFrame(
        {
            "source_row": txns["source_row"].to_numpy(),
            "merchant_name": txns["merchant_name"].to_numpy(),
            "best_candidate_station_id": None,
            "best_confidence": np.nan,
            "reason": reason,
        }
    )
    return MatchResult(matches=matches, unmatched=unmatched)

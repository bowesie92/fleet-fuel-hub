"""synth/messiness.py: turn clean truth into deliberately messy vendor exports, and record every flaw.

Business terms: real exports are untidy. This module makes the fuel card file untidy
(station names written four different ways, duplicate swipes, blank or mistyped odometers)
and the GPS file untidy (pings at irregular times, outages, timestamps in UTC). Every flaw
it injects is written down as ground truth so the cleaning pipeline can be scored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .geo import METERS_PER_DEGREE
from .ops import Fill, TruckDay
from .params import Params

MERCHANT_CODE_DIGITS = 6  # Setting: digits in a 'merchant_code' name such as MERCH 004512
MERCHANT_CODE_PREFIX = "MERCH"
TRANSACTION_ID_MAX = 9_999_999  # Contract 4.7: FC + 7 digits
SECONDS_PER_MINUTE = 60
LOCAL_FMT = "%Y-%m-%d %H:%M:%S"

NAME_VARIANTS = ("canonical", "upper_store", "short_brand", "merchant_code")


@dataclass
class MerchantProfiles:
    """The fixed store number and merchant code the card vendor uses for each station."""

    store_no: np.ndarray
    code: list[str]


def merchant_profiles(n_stations: int, rng: np.random.Generator, store_min: int, store_max: int) -> MerchantProfiles:
    """Invent a stable store number and a unique merchant code for every station."""
    store_no = rng.integers(store_min, store_max + 1, size=n_stations)
    base = 10 ** (MERCHANT_CODE_DIGITS - 1)
    nums = rng.choice(9 * base, size=n_stations, replace=False) + base
    return MerchantProfiles(store_no, [f"{MERCHANT_CODE_PREFIX} {int(n):0{MERCHANT_CODE_DIGITS}d}" for n in nums])


def _clean_upper(text: str) -> str:
    """Upper-case, drop punctuation and any '#12' store token, squeeze spaces."""
    text = re.sub(r"#\s*\d+", "", text.upper())
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", "", text)).strip()


def _text(value) -> str | None:
    """A non-empty string, or None (missing values may arrive as None or NaN)."""
    return value if isinstance(value, str) and value else None


def merchant_name(station: pd.Series, variant: str, store_no: int, code: str, unnamed_label: str) -> str:
    """Write a station's name the way the card vendor might: one of four variants.

    A station with no OSM name and no brand is shown by the vendor as a generic label plus its
    stable store number ("FUEL STOP #471"), never with our internal station ID. Its short_brand
    variant is the bare label, which is deliberately weak evidence.
    """
    if not (_text(station["name"]) or _text(station["brand"])):
        label = unnamed_label.upper()
        if variant == "merchant_code":
            return code
        return label if variant == "short_brand" else f"{label} #{store_no}"
    canonical = _text(station["name"]) or _text(station["brand"])
    if variant == "canonical":
        return canonical
    if variant == "merchant_code":
        return code
    brand, city_text = _text(station["brand"]), _text(station["city"])
    base = brand or _text(station["name"]) or canonical
    if variant == "upper_store":
        city = f" {_clean_upper(city_text)}" if city_text else ""
        return f"{_clean_upper(base)} #{store_no}{city}"
    short = brand or canonical.split()[0]
    if short == canonical:
        short = _clean_upper(short)
    return short


def wrong_odometer(true_value: int, min_error: float, rng: np.random.Generator, attempts: int,
                   fallback_multiple: float) -> int:
    """Make a believable driver typo (swapped, dropped, extra or changed digit) that is clearly wrong."""
    digits = str(true_value)
    for _ in range(attempts):
        kind = int(rng.integers(4))
        pos = int(rng.integers(len(digits)))
        if kind == 0 and len(digits) >= 2:
            pos = min(pos, len(digits) - 2)
            typo = digits[:pos] + digits[pos + 1] + digits[pos] + digits[pos + 2 :]
        elif kind == 1 and len(digits) >= 2:
            typo = digits[:pos] + digits[pos + 1 :]
        elif kind == 2:
            typo = digits[:pos] + str(int(rng.integers(10))) + digits[pos:]
        else:
            typo = digits[:pos] + str(int(rng.integers(10))) + digits[pos + 1 :]
        value = int(typo)
        if value > 0 and abs(value - true_value) > min_error:
            return value
    return true_value + int(fallback_multiple * min_error)


def build_fuel_card(fills: list[Fill], fleet: pd.DataFrame, stations: pd.DataFrame, profiles: MerchantProfiles,
                    p: Params, rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    """Make fuel_card.csv rows, the matching ground-truth rows, and one issue record per injected flaw."""
    card_of = dict(zip(fleet["truck_id"], fleet["fuel_card_last4"]))
    names, shares = zip(*p.name_variant_mix)
    cdf = np.cumsum(shares)
    ordered = sorted(enumerate(fills), key=lambda x: (x[1].ts, x[1].truck_id, x[0]))
    rows: list[dict] = []
    for event_no, (_, f) in enumerate(ordered, start=1):
        variant = names[min(int(np.searchsorted(cdf, rng.random(), side="right")), len(names) - 1)]
        u = rng.random()
        true_int = int(round(f.odometer))
        if u < p.missing_odometer_rate:
            issue, shown = "missing", ""
        elif u < p.missing_odometer_rate + p.wrong_odometer_rate:
            issue, shown = "wrong", str(wrong_odometer(true_int, p.odometer_min_error_mi, rng, p.wrong_odometer_attempts,
                                                     p.wrong_odometer_fallback_multiple))
        else:
            issue, shown = "none", str(true_int)
        is_dup = rng.random() < p.duplicate_rate
        st = stations.iloc[f.station_idx]
        gallons = round(f.gallons, 3)
        ppg = round(f.net_price, 3)
        row = {
            "ts": f.ts, "truck_id": f.truck_id, "event_no": event_no, "is_dup": False,
            "CardLast4": card_of[f.truck_id], "MerchantName": merchant_name(
                st, variant, int(profiles.store_no[f.station_idx]), profiles.code[f.station_idx], p.unnamed_label),
            "MerchantCity": _text(st["city"]) or "", "Gallons": gallons, "PPG": ppg,
            "Total": round(gallons * ppg, 2), "Odometer": shown,
            "true_event_id": f"EVT-{event_no:06d}", "true_station_id": st["station_id"],
            "true_odometer": round(f.odometer, 1), "odometer_issue": issue, "name_variant": variant,
            "fill_behavior": f.behavior, "route_id": f.route_id,
        }
        rows.append(row)
        if is_dup:
            rows.append({**row, "is_dup": True})
    rows.sort(key=lambda r: (r["ts"], r["truck_id"], r["is_dup"], r["event_no"]))
    ids = rng.choice(TRANSACTION_ID_MAX, size=len(rows), replace=False) + 1
    frame = pd.DataFrame(rows)
    frame["TransactionID"] = [f"FC{int(i):07d}" for i in ids]
    frame["source_row"] = np.arange(1, len(frame) + 1)
    card = pd.DataFrame(
        {
            "TransactionID": frame["TransactionID"],
            "CardLast4": frame["CardLast4"],
            "Unit": frame["truck_id"],
            "TransDate": frame["ts"].dt.strftime("%m/%d/%Y"),
            "TransTime": frame["ts"].dt.strftime("%H:%M"),
            "MerchantName": frame["MerchantName"],
            "MerchantCity": frame["MerchantCity"],
            "MerchantState": "AZ",
            "Product": "ULSD",
            "Gallons": frame["Gallons"],
            "PPG": frame["PPG"],
            "Total": frame["Total"],
            "Odometer": frame["Odometer"],
        }
    )
    truth = pd.DataFrame(
        {
            "source_row": frame["source_row"],
            "TransactionID": frame["TransactionID"],
            "true_event_id": frame["true_event_id"],
            "is_duplicate": frame["is_dup"],
            "truck_id": frame["truck_id"],
            "true_station_id": frame["true_station_id"],
            "true_ts_local": frame["ts"],
            "true_odometer": frame["true_odometer"],
            "odometer_issue": frame["odometer_issue"],
            "name_variant": frame["name_variant"],
            "fill_behavior": frame["fill_behavior"],
            "route_id": frame["route_id"],
        }
    )
    return card, truth, fuel_card_issues(frame)


def fuel_card_issues(frame: pd.DataFrame) -> list[dict]:
    """One issue record per messy fuel card row: odd name, duplicate swipe, blank or mistyped odometer."""
    out: list[dict] = []

    def add(kind: str, row, detail: str) -> None:
        out.append({"issue_type": kind, "source_file": "fuel_card.csv", "source_row": int(row.source_row),
                    "truck_id": row.truck_id, "start_ts_local": None, "end_ts_local": None, "details": detail})

    for row in frame.itertuples():
        if row.name_variant != "canonical":
            add("name_variant", row, f"Merchant shown as '{row.MerchantName}' ({row.name_variant}) for {row.true_station_id}")
    for row in frame.itertuples():
        if row.is_dup:
            add("duplicate_swipe", row, f"Second copy of {row.true_event_id}: same card, minute and amount, new TransactionID")
    for row in frame.itertuples():
        if row.odometer_issue == "missing":
            add("missing_odometer", row, "Odometer left blank by the driver")
    for row in frame.itertuples():
        if row.odometer_issue == "wrong":
            add("wrong_odometer", row, f"Odometer typed as {row.Odometer}, true reading {row.true_odometer}")
    return out


# ----- telematics ----------------------------------------------------------------


def _move_pings(item: tuple, p: Params, rng: np.random.Generator, out: list[tuple]) -> None:
    """Drop pings along a move at random intervals, with the position interpolated on the straight path."""
    _, t0, t1, lat0, lon0, lat1, lon1, odo0, odo1, speed = item
    duration = (t1 - t0).total_seconds()
    elapsed = rng.uniform(p.ping_min, p.ping_max) * SECONDS_PER_MINUTE
    while elapsed < duration:
        frac = elapsed / duration
        mph = max(1, int(round(rng.normal(speed, speed * p.ping_speed_sd))))
        out.append((t0 + timedelta(seconds=round(elapsed)), lat0 + (lat1 - lat0) * frac,
                    lon0 + (lon1 - lon0) * frac, odo0 + (odo1 - odo0) * frac, mph))
        elapsed += rng.uniform(p.ping_min, p.ping_max) * SECONDS_PER_MINUTE


def _day_pings(td: TruckDay, p: Params, rng: np.random.Generator) -> list[tuple]:
    """Every ping a truck would send in a day: arrival and departure at each stop, irregular pings between."""
    out: list[tuple] = []
    for item in td.items:
        if item[0] == "stop":
            _, t_arr, t_dep, lat, lon, odo = item
            out.append((t_arr, lat, lon, odo, 0))
            if t_dep > t_arr:
                out.append((t_dep, lat, lon, odo, 0))
        else:
            _move_pings(item, p, rng, out)
    return out


def generate_pings(days: list[TruckDay], p: Params, rng: np.random.Generator) -> tuple[pd.DataFrame, list[dict]]:
    """Make the telematics rows (local time, not yet converted to UTC) and one record per GPS outage."""
    cols: dict[str, list] = {"device_id": [], "ts": [], "lat": [], "lon": [], "odometer_mi": [], "speed_mph": []}
    gaps: list[dict] = []
    noise_deg = p.ping_noise_m / METERS_PER_DEGREE
    for td in days:
        pts = _day_pings(td, p, rng)
        has_gap = rng.random() < p.gap_rate
        length = timedelta(minutes=float(rng.uniform(p.gap_min, p.gap_max)))
        # An outage must start after the first ping and leave at least gap_min before the day's last ping.
        starts = [i for i in range(1, len(pts) - 1) if pts[i][0] + timedelta(minutes=p.gap_min) <= pts[-1][0]]
        if has_gap and starts:
            first = starts[int(rng.integers(len(starts)))]
            cutoff = pts[first][0] + length
            keep = [i for i, pt in enumerate(pts) if i < first or i == len(pts) - 1 or pt[0] > cutoff]
            dropped = len(pts) - len(keep)
            after = next(i for i in keep if i >= first)
            gaps.append({"issue_type": "gps_gap", "source_file": "telematics.csv", "source_row": None,
                         "truck_id": td.truck_id, "start_ts_local": pts[first - 1][0], "end_ts_local": pts[after][0],
                         "details": f"No GPS pings between these two times; {dropped} expected pings missing"})
            pts = [pts[i] for i in keep]
        jitter = rng.normal(0.0, noise_deg, size=(len(pts), 2))
        for (t, lat, lon, odo, mph), (dlat, dlon) in zip(pts, jitter):
            cols["device_id"].append(td.device_id)
            cols["ts"].append(t)
            cols["lat"].append(lat + dlat)
            cols["lon"].append(lon + dlon)
            cols["odometer_mi"].append(odo)
            cols["speed_mph"].append(mph)
    frame = pd.DataFrame(cols)
    frame["ts"] = pd.to_datetime(frame["ts"])
    frame["lat"] = frame["lat"].round(5)
    frame["lon"] = frame["lon"].round(5)
    frame["odometer_mi"] = frame["odometer_mi"].round(1)
    frame = frame.sort_values(["device_id", "ts", "odometer_mi"], kind="stable").reset_index(drop=True)
    return frame, gaps


def to_export_time(local_ts: pd.Series, p: Params) -> pd.Series:
    """Convert naive local Arizona times to the vendor's export timezone (UTC), as naive text."""
    converted = local_ts.dt.tz_localize(p.tz).dt.tz_convert(p.export_tz).dt.tz_localize(None)
    return converted.dt.strftime(LOCAL_FMT)


def summary_issues(p: Params, n_pings: int) -> tuple[dict, dict]:
    """The two whole-file issue rows: irregular ping intervals, and UTC timestamps."""
    base = {"source_file": "telematics.csv", "source_row": None, "truck_id": None,
            "start_ts_local": None, "end_ts_local": None}
    irregular = {**base, "issue_type": "irregular_ping",
                 "details": f"Pings arrive every {p.ping_min:g} to {p.ping_max:g} minutes while moving, "
                            f"plus one at each stop arrival and departure ({n_pings} pings in total)"}
    utc = {**base, "issue_type": "timezone_utc",
           "details": f"All event_time values are exported in {p.export_tz}, not {p.tz} local time"}
    return irregular, utc


def number_issues(groups: list[list[dict]]) -> pd.DataFrame:
    """Join issue records in the given order and assign ISS-00001 style IDs."""
    cols = ["issue_id", "issue_type", "source_file", "source_row", "truck_id", "start_ts_local", "end_ts_local", "details"]
    records = [r for group in groups for r in group]
    frame = pd.DataFrame(records, columns=[c for c in cols if c != "issue_id"])
    frame.insert(0, "issue_id", [f"ISS-{i:05d}" for i in range(1, len(frame) + 1)])
    frame["source_row"] = frame["source_row"].astype("Int64")
    for col in ("start_ts_local", "end_ts_local"):
        frame[col] = pd.to_datetime(frame[col]).dt.strftime(LOCAL_FMT)
    return frame

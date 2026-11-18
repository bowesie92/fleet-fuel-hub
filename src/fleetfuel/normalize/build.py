"""Builders for the dimension and daily tables that are not cleaning steps in themselves.

dim_station (stations plus partner discounts plus the merchant names seen on matched swipes),
dim_route (stops, planned miles and minutes, a straight-line shape) and fact_route_day
(actual miles per truck per day from the GPS odometer).
"""

from __future__ import annotations

import json

import pandas as pd

from fleetfuel import config

SECONDS_PER_MINUTE = 60
MINUTES_PER_DAY = 24 * 60  # a planned time earlier than the route's first stop belongs to the next day
CUSTOMER_LABEL_DIGITS = 3  # "Customer 014": the last 3 digits of the customer number (contracts section 9)


def _blank(value) -> bool:
    """True for None, NaN and empty text."""
    return value is None or (isinstance(value, float) and pd.isna(value)) or value == ""


def build_dim_station(
    stations: pd.DataFrame, partners: pd.DataFrame, aliases: dict[str, dict[str, list[str]]]
) -> pd.DataFrame:
    """One row per station, with partner status and the fuel card names and codes that matched it."""
    discount = dict(zip(partners["station_id"], partners["partner_discount_cpg"]))
    rows = []
    for s in stations.itertuples(index=False):
        names = aliases.get(s.station_id, {"fuel_card_names": [], "merchant_codes": []})
        source_ids = {"osm_ref": None if _blank(s.osm_ref) else s.osm_ref, "merged_osm_refs": json.loads(s.merged_osm_refs), "fuel_card_names": names["fuel_card_names"], "merchant_codes": names["merchant_codes"]}
        if not _blank(s.add_id):  # confirmed or created from a chain locator (contract 4.11)
            source_ids["source_url"], source_ids["add_id"] = s.source_url, s.add_id
        rows.append(
            {
                "station_id": s.station_id,
                "name": s.name,
                "brand": s.brand,
                "lat": s.lat,
                "lon": s.lon,
                "station_class": s.station_class,
                "is_partner": s.station_id in discount,
                "partner_discount_cpg": float(discount.get(s.station_id, 0.0)),
                "source_ids_json": json.dumps(source_ids),
                "truck_access": s.truck_access,
                "access_basis": s.access_basis,
            }
        )
    return pd.DataFrame(rows)


PLANNED_ARRIVAL_FORMAT = "%H:%M"


def location_label(cfg: dict, location_id: str) -> str:
    """Display label for a stop: the yard names from config, or "Customer 014" for CUST-0014."""
    for yard in ("phoenix_dc", "tucson_yard"):
        if location_id == config.get(cfg, f"yards.{yard}.id"):
            return config.get(cfg, f"yards.{yard}.label")
    digits = "".join(ch for ch in location_id if ch.isdigit())
    if digits:
        return f"Customer {int(digits) % 10**CUSTOMER_LABEL_DIGITS:0{CUSTOMER_LABEL_DIGITS}d}"
    return location_id


def build_dim_route(plans_local: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, int]:
    """One row per route from its first planned day. Returns (table, routes whose stop list changes over time).

    Each stop carries planned_arrival (HH:MM): the planned time the truck reaches this stop, as written in the route plan
    export, so a fuel stop can be priced for the time of day it happens.
    """
    rows = []
    changing = 0
    for route_id, plan in plans_local.groupby("route_id"):
        by_day = {day: g.sort_values("stop_seq") for day, g in plan.groupby("plan_date")}
        if len({tuple(g["location_id"]) for g in by_day.values()}) > 1:
            changing += 1
        first = by_day[min(by_day)]
        stops = [
            {
                "seq": int(r.stop_seq),
                "location_id": r.location_id,
                "label": location_label(cfg, r.location_id),
                "lat": float(r.lat),
                "lon": float(r.lon),
                "planned_arrival": r.planned_ts_source.strftime(PLANNED_ARRIVAL_FORMAT),
            }
            for r in first.itertuples(index=False)
        ]
        minutes = (first["planned_ts_local"].iloc[-1] - first["planned_ts_local"].iloc[0]).total_seconds() / SECONDS_PER_MINUTE
        if minutes < 0:
            minutes += MINUTES_PER_DAY
        line = {"type": "LineString", "coordinates": [[s["lon"], s["lat"]] for s in stops]}
        rows.append(
            {
                "route_id": route_id,
                "yard": stops[0]["location_id"],
                "stops_json": json.dumps(stops),
                "base_miles": float(first["planned_miles_from_prev"].sum()),
                "base_minutes": float(minutes),
                "geometry_geojson": json.dumps(line),
            }
        )
    return pd.DataFrame(rows), changing


def build_fact_route_day(plans_local: pd.DataFrame, pings_local: pd.DataFrame) -> pd.DataFrame:
    """One row per planned route per truck per day, with miles driven from the GPS odometer.

    actual_miles is the odometer range between the day's first and last ping; it is NULL if the truck has no pings that day.
    """
    days = plans_local.assign(date=plans_local["plan_date"].dt.normalize())[["route_id", "truck_id", "date"]].drop_duplicates()
    daily = pings_local.dropna(subset=["odometer"]).assign(date=lambda d: d["ts_local"].dt.normalize())
    span = daily.groupby(["truck_id", "date"])["odometer"].agg(["max", "min"])
    miles = (span["max"] - span["min"]).rename("actual_miles").reset_index()
    out = days.merge(miles, on=["truck_id", "date"], how="left")
    return out.sort_values(["date", "route_id"]).reset_index(drop=True)

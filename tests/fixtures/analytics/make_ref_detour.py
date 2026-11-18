"""Build tests/fixtures/analytics/ref_detour_sample.csv: a stand-in for routing's ref_detour.

Run from the repo root after `uv run fleetfuel build` has loaded data/hub.duckdb:
    uv run python tests/fixtures/analytics/make_ref_detour.py

It is NOT road routing. Each extra distance is a straight-line estimate (station to the two
neighbouring stops, minus the stops' own gap) times the config circuity factor, and extra minutes
come from the config average speeds. Only stations within `routing.candidate_station_radius_mi`
of a stop are kept, as the real step will do. The real file comes from routing-engineer.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import duckdb

from fleetfuel import config

OUT = Path(__file__).with_name("ref_detour_sample.csv")


def miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Straight-line miles between two (lat, lon) points."""
    r = 3958.8
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def main() -> None:
    cfg = config.load_assumptions()
    circuity = config.get(cfg, "routes.straight_line_circuity")
    radius = config.get(cfg, "routing.candidate_station_radius_mi")
    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    stations = con.execute("SELECT station_id, lat, lon FROM dim_station ORDER BY station_id").fetchall()
    lines = ["route_id,station_id,insert_after_stop,extra_miles,extra_minutes"]
    for route_id, yard, stops_json in con.execute("SELECT route_id, yard, stops_json FROM dim_route ORDER BY route_id").fetchall():
        stops = [(s["lat"], s["lon"]) for s in json.loads(stops_json)]
        speed = config.get(cfg, "routes.avg_speed_mph_linehaul" if route_id.startswith("PHX") else "routes.avg_speed_mph_local")
        for sid, lat, lon in stations:
            if min(miles((lat, lon), s) for s in stops) > radius:
                continue
            for k in range(len(stops) - 1):
                extra = circuity * (miles(stops[k], (lat, lon)) + miles((lat, lon), stops[k + 1]) - miles(stops[k], stops[k + 1]))
                lines.append(f"{route_id},{sid},{k},{extra:.2f},{extra / speed * 60:.1f}")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {len(lines) - 1} rows to {OUT}")


if __name__ == "__main__":
    main()

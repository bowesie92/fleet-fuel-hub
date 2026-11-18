"""synth/generator.py: build the whole synthetic world and write every owned file.

Business terms: this is the conductor. It reads the public inputs, creates the fleet and
fixed routes, prices every station for every day, simulates a year of driving and fueling,
makes the messy vendor exports, and writes the ground truth that records what really
happened. Everything is seeded, so two runs with the same config give identical files.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from fleetfuel import config

from . import messiness, ops, prices, routes
from .inputs import FIXTURE_DIR, Inputs, load_inputs
from .params import Params, load_params

DEVICE_ID_DIGITS = 5  # Contract 4.4: GPS- plus 5 digits
CARD_LAST4_SPACE = 10_000  # Contract 4.4: 4-digit card endings
GROUND_TRUTH_SUBDIR = "ground_truth"
GALLON_DECIMALS = 3  # Contract 4.12
PRICE_DECIMALS = 3  # Contract 4.12
TOTAL_DECIMALS = 2  # Contract 4.12
RNG_STREAMS = ("fleet", "routes", "prices", "ops", "merchants", "card", "pings")


def streams(seed: int) -> dict[str, np.random.Generator]:
    """One independent random stream per component, so changing one part never reshuffles another."""
    children = np.random.SeedSequence(seed).spawn(len(RNG_STREAMS))
    return {name: np.random.default_rng(child) for name, child in zip(RNG_STREAMS, children)}


def build_fleet(p: Params, rng: np.random.Generator) -> pd.DataFrame:
    """The clean truck master list (contract 4.4): linehaul tractors in Phoenix, local tractors in Tucson."""
    n = p.linehaul_trucks + p.local_trucks
    device_nums = rng.choice(10**DEVICE_ID_DIGITS, size=n, replace=False)
    card_nums = rng.choice(CARD_LAST4_SPACE, size=n, replace=False)
    rows = []
    for i in range(p.linehaul_trucks):
        rows.append((f"PHX-{i + 1:02d}", p.phx.id, "linehaul", p.linehaul_tank_gal, p.linehaul_mpg))
    for i in range(p.local_trucks):
        rows.append((f"TUC-{i + 1:02d}", p.tuc.id, "local", p.local_tank_gal, p.local_mpg))
    frame = pd.DataFrame(rows, columns=["truck_id", "home_yard", "role", "tank_gal", "mpg_assumed"])
    frame["telematics_device_id"] = [f"GPS-{int(x):0{DEVICE_ID_DIGITS}d}" for x in device_nums]
    frame["fuel_card_last4"] = [f"{int(x):04d}" for x in card_nums]
    return frame


def plan_table(p: Params, route_list: list[routes.Route], fleet: pd.DataFrame) -> pd.DataFrame:
    """The route_plans.csv rows (contract 4.9): one per planned stop per route per operating day."""
    by_id = {r.route_id: r for r in route_list}
    rows = []
    for day, route_id, truck_id in ops.assignments(p, route_list, fleet):
        for s in by_id[route_id].stops:
            minutes = int(round(s.arrive_min))
            rows.append((day, route_id, truck_id, s.seq, s.stop_type, s.location_id, s.lat, s.lon,
                         f"{minutes // 60:02d}:{minutes % 60:02d}", s.miles_from_prev))
    cols = ["PlanDate", "RouteID", "Vehicle", "StopSeq", "StopType", "LocationID", "Lat", "Lon",
            "PlannedArrival", "PlannedMilesFromPrev"]
    return pd.DataFrame(rows, columns=cols).sort_values(["PlanDate", "RouteID", "StopSeq"], kind="stable").reset_index(drop=True)


# ----- writing ----------------------------------------------------------------------


def _fixed(series: pd.Series, decimals: int) -> pd.Series:
    """Format numbers with a fixed number of decimals so files are identical run to run."""
    return pd.Series(np.char.mod(f"%.{decimals}f", series.to_numpy(dtype=float)), index=series.index)


def _bool_text(series: pd.Series) -> pd.Series:
    """Booleans written as true / false (contract rule 8)."""
    return series.map({True: "true", False: "false"})


def write_csv(frame: pd.DataFrame, path: Path) -> int:
    """Write UTF-8, comma-separated, LF line endings, no index. Returns the data row count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n", encoding="utf-8")
    return len(frame)


def write_all(out_dir: Path, tables: dict[str, pd.DataFrame], manifest: dict) -> dict[str, int]:
    """Write every owned CSV plus the manifest; returns row counts keyed by relative file name."""
    counts = {name: write_csv(frame, out_dir / name) for name, frame in tables.items()}
    manifest["row_counts"] = counts
    path = out_dir / GROUND_TRUTH_SUBDIR / "run_manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    return counts


def yard_fuel_table(yard_fills: list[ops.YardFill]) -> pd.DataFrame:
    """The yard_fuel.csv rows (contract 4.12): on-site tank fills, clean, sorted by date, time, unit."""
    rows = [(f.ts.date(), f.ts.strftime("%H:%M"), f.yard_id, f.truck_id, f.gallons, f.price) for f in yard_fills]
    out = pd.DataFrame(rows, columns=["FillDate", "FillTime", "Yard", "Unit", "Gallons", "PricePerGal"])
    out["Gallons"] = out["Gallons"].round(GALLON_DECIMALS)
    out["PricePerGal"] = out["PricePerGal"].round(PRICE_DECIMALS)
    out["Total"] = (out["Gallons"] * out["PricePerGal"]).round(TOTAL_DECIMALS)
    return out.sort_values(["FillDate", "FillTime", "Unit"], kind="stable").reset_index(drop=True)


def format_tables(fleet, partners, price_rows, card, pings, plans, truth, issues, yard) -> dict[str, pd.DataFrame]:
    """Apply the contract's number and boolean formats to each table, keyed by output file name."""
    fleet = fleet.assign(tank_gal=_fixed(fleet["tank_gal"], 1), mpg_assumed=_fixed(fleet["mpg_assumed"], 1))
    partners = partners.assign(partner_discount_cpg=_fixed(partners["partner_discount_cpg"], 1))
    price_rows = price_rows.assign(posted_price_per_gal=_fixed(price_rows["posted_price_per_gal"], 3))
    card = card.assign(Gallons=_fixed(card["Gallons"], 3), PPG=_fixed(card["PPG"], 3), Total=_fixed(card["Total"], 2))
    pings = pings.assign(lat=_fixed(pings["lat"], 5), lon=_fixed(pings["lon"], 5),
                         odometer_mi=_fixed(pings["odometer_mi"], 1))
    plans = plans.assign(Lat=_fixed(plans["Lat"], 5), Lon=_fixed(plans["Lon"], 5),
                         PlannedMilesFromPrev=_fixed(plans["PlannedMilesFromPrev"], 1))
    truth = truth.assign(is_duplicate=_bool_text(truth["is_duplicate"]),
                         true_ts_local=truth["true_ts_local"].dt.strftime(messiness.LOCAL_FMT),
                         true_odometer=_fixed(truth["true_odometer"], 1))
    yard = yard.assign(Gallons=_fixed(yard["Gallons"], GALLON_DECIMALS),
                       PricePerGal=_fixed(yard["PricePerGal"], PRICE_DECIMALS), Total=_fixed(yard["Total"], TOTAL_DECIMALS))
    return {
        "fleet.csv": fleet,
        "partner_program.csv": partners,
        "station_prices.csv": price_rows,
        "fuel_card.csv": card,
        "telematics.csv": pings,
        "route_plans.csv": plans,
        "yard_fuel.csv": yard,
        f"{GROUND_TRUTH_SUBDIR}/fuel_txn_truth.csv": truth,
        f"{GROUND_TRUTH_SUBDIR}/injected_issues.csv": issues,
    }


# ----- the run ----------------------------------------------------------------------


def generate(p: Params, inputs: Inputs) -> tuple[dict[str, pd.DataFrame], prices.PriceBook]:
    """Build every output table in memory from parameters and inputs. Pure and seeded."""
    rng = streams(p.seed)
    stations = inputs.stations
    fleet = build_fleet(p, rng["fleet"])
    route_list = routes.build_local_routes(p, inputs.customers, rng["routes"]) + routes.build_linehaul_routes(p)
    book = prices.build_price_book(p, stations, inputs.eia, rng["prices"])
    fills, truck_days, yard_fills = ops.Simulator(p, fleet, route_list, stations, book, rng["ops"]).run()
    profiles = messiness.merchant_profiles(len(stations), rng["merchants"], p.store_number_min, p.store_number_max)
    card, truth, card_issues = messiness.build_fuel_card(fills, fleet, stations, profiles, p, rng["card"])
    pings, gaps = messiness.generate_pings(truck_days, p, rng["pings"])
    irregular, utc = messiness.summary_issues(p, len(pings))
    issues = messiness.number_issues([
        [i for i in card_issues if i["issue_type"] == kind]
        for kind in ("name_variant", "duplicate_swipe", "missing_odometer", "wrong_odometer")
    ] + [[irregular], gaps, [utc]])
    pings = pings.assign(event_time=messiness.to_export_time(pings["ts"], p)).rename(columns={"ts": "local_ts"})
    pings = pings[["device_id", "event_time", "lat", "lon", "odometer_mi", "speed_mph"]]
    tables = format_tables(fleet, prices.partner_table(stations, book), prices.price_table(stations, book),
                           card, pings, plan_table(p, route_list, fleet), truth, issues, yard_fuel_table(yard_fills))
    return tables, book


def run(cfg: dict, raw_dir: Path | None = None, out_dir: Path | None = None,
        fixture_dir: Path | None = None, overrides_path: Path | None = None) -> dict[str, int]:
    """Entry point called by the CLI (contract section 8): write every synth-owned file.

    raw_dir is where the real public inputs are looked for (default data/raw); out_dir is
    where the files are written (default raw_dir). Missing inputs fall back to fixtures.
    """
    p = load_params(cfg)
    raw = Path(raw_dir) if raw_dir else config.RAW_DIR
    out = Path(out_dir) if out_dir else raw
    inputs = load_inputs(raw, Path(fixture_dir) if fixture_dir else FIXTURE_DIR, overrides_path)
    print(f"[synth] inputs: {inputs.sources}")
    tables, book = generate(p, inputs)
    if book.carry_forward_days:
        print(f"[synth] note: {book.carry_forward_days} period days fall after the last EIA week; its price was carried forward")
    if book.backfill_days:
        print(f"[synth] note: {book.backfill_days} period days fall before the first EIA week; its price was used")
    manifest = {"seed": p.seed, "period_start": p.start.isoformat(), "period_end": p.end.isoformat(),
                "row_counts": {}, "inputs": inputs.sources}
    counts = write_all(out, tables, manifest)
    for name, n in counts.items():
        print(f"[synth] wrote {name}: {n} rows")
    return counts

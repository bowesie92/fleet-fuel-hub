"""The three hand-checked missed-savings fills of docs/hand-checks.md, checked against the real committed data.

Fills FC5659816, FC8960414 and FC4426227 (contract v1.16 data). For each one the test:
  1. reads the truck, time, gallons and price paid from data/raw/fuel_card.csv and the alternative's posted
     price that day from data/raw/station_prices.csv (none of the three alternatives is a partner, so net = posted);
  2. takes the rush-hour factor from multiplier() at the fill's own timestamp (never a constant);
  3. prices both detours with the shared cost function (cost_parts), on the full-precision detour inputs below;
  4. compares each part and the net to the figures worked by hand in docs/hand-checks.md, to the cent;
  5. compares the rounded rows of site/data/missed.json with the same figures.
If an assumption changes, redo the arithmetic and update docs/hand-checks.md and this file together.

The formula (docs/hand-checks.md):
    net saving = price gap x gallons + detour cost at the station used - detour cost to the alternative
    detour cost = (miles / mpg) x price + minutes x factor / 60 x driver cost per hour + miles x wear per mile
with mpg 6.0, driver $38.00 an hour and wear $0.25 a mile (all read from config/assumptions.yaml here).
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from fleetfuel import config
from fleetfuel.analytics.net_savings import SavingsParams, cost_parts, multiplier

ROOT = Path(__file__).resolve().parents[1]
FUEL_CARD = ROOT / "data" / "raw" / "fuel_card.csv"
STATION_PRICES = ROOT / "data" / "raw" / "station_prices.csv"
MISSED = ROOT / "site" / "data" / "missed.json"

# Full-precision detour inputs from ref_detour (the doc shows them to 4 decimals).
# Each is (miles, minutes) of the detour, before the rush-hour factor.
CASES = {
    "FC5659816": {
        "truck": "TUC-11", "alt_station": "STN-A03", "used": (21.084740117712556, 36.00366666666666), "alt": (3.3396713194941543, 7.8515000000000015),
        "factor": 1.17, "timestamp": "2025-12-05 07:02",
        # Price gap: (4.017 - 3.891) x 76.960 = 0.126 x 76.960 = 9.6970
        # Used (Pilot Express) detour: fuel 21.0847/6 x 4.017 = 14.1162 ; time 36.0037 x 1.17 = 42.1243 min x 38/60 = 26.6787 ; wear 21.0847 x 0.25 = 5.2712 ; total 46.0661
        # Alternative detour: fuel 3.3397/6 x 3.891 = 2.1658 ; time 7.8515 x 1.17 = 9.1863 min x 38/60 = 5.8180 ; wear 3.3397 x 0.25 = 0.8349 ; total 8.8187
        # Net: 9.6970 + 46.0661 - 8.8187 = 46.9444 -> 46.94
        "gap": 9.6970, "used_cost": 46.0661, "alt_cost": 8.8187, "net_exact": 46.9444, "net": 46.94,
        # Own rounding gives 9.70 + 46.07 - 8.82 = 46.95, a cent over. The site shows the alt detour as 8.83 so the parts add to 46.94 (contract 11.5).
        "cents": (9.70, 46.07, 8.82), "shown": (9.70, 46.07, 8.83),
        "why": "both", "why_line": "Lower pump price and saves a 17.8-mile detour.",
    },
    "FC8960414": {
        "truck": "TUC-10", "alt_station": "STN-A02", "used": (0.6793761930326893, 1.5566666666666662), "alt": (0.7520269128290784, 2.2723333333333318),
        "factor": 1.0, "timestamp": "2026-10-28 05:15",
        # Price gap: (6.501 - 6.375) x 73.606 = 0.126 x 73.606 = 9.2744
        # Used detour: fuel 0.6794/6 x 6.501 = 0.7361 ; time 1.5567 x 1 = 1.5567 min x 38/60 = 0.9859 ; wear 0.6794 x 0.25 = 0.1698 ; total 1.8918
        # Alternative detour: fuel 0.7520/6 x 6.375 = 0.7990 ; time 2.2723 x 1 = 2.2723 min x 38/60 = 1.4391 ; wear 0.7520 x 0.25 = 0.1880 ; total 2.4262
        # Net: 9.2744 + 1.8918 - 2.4262 = 8.7400 -> 8.74
        "gap": 9.2744, "used_cost": 1.8918, "alt_cost": 2.4262, "net_exact": 8.7400, "net": 8.74,
        # Own rounding gives 9.27 + 1.89 - 2.43 = 8.73, a cent under. The site shows the alt detour as 2.42 so the parts add to 8.74.
        "cents": (9.27, 1.89, 2.43), "shown": (9.27, 1.89, 2.42),
        "why": "lower price", "why_line": "Lower pump price, worth the extra 0.1-mile detour.",
    },
    "FC4426227": {
        "truck": "TUC-07", "alt_station": "STN-A03", "used": (6.546928437922531, 8.45466666666667), "alt": (0.13279323749304067, 0.6048333333333328),
        "factor": 1.17, "timestamp": "2026-10-30 08:47",
        # Price gap: (6.394 - 6.489) x 79.289 = -0.095 x 79.289 = -7.5325 (the option costs more per gallon)
        # Used detour: fuel 6.5469/6 x 6.394 = 6.9768 ; time 8.4547 x 1.17 = 9.8920 min x 38/60 = 6.2649 ; wear 6.5469 x 0.25 = 1.6367 ; total 14.8785
        # Alternative detour: fuel 0.1328/6 x 6.489 = 0.1436 ; time 0.6048 x 1.17 = 0.7077 min x 38/60 = 0.4482 ; wear 0.1328 x 0.25 = 0.0332 ; total 0.6250
        # Net: -7.5325 + 14.8785 - 0.6250 = 6.7210 -> 6.72
        "gap": -7.5325, "used_cost": 14.8785, "alt_cost": 0.6250, "net_exact": 6.7210, "net": 6.72,
        # The alt detour is 0.62498 at full precision. Own rounding gives -7.53 + 14.88 - 0.62 = 6.73, a cent over. The site shows 0.63 so the parts add to 6.72.
        "cents": (-7.53, 14.88, 0.62), "shown": (-7.53, 14.88, 0.63),
        "why": "shorter detour", "why_line": "Higher pump price, but saves a 6.4-mile detour.",
    },
}
TXNS = list(CASES)

# The other inputs exactly as docs/hand-checks.md writes them:
# (route, used station, alt station, gallons, price paid, alt price, used inserted after stop, alt inserted after stop).
DOC_INPUTS = {
    "FC5659816": ("TUC-R10", "STN-032", "STN-A03", 76.96, 4.017, 3.891, 10, 0),
    "FC8960414": ("TUC-R09", "STN-A03", "STN-A02", 73.606, 6.501, 6.375, 11, 3),
    "FC4426227": ("TUC-R04", "STN-A02", "STN-A03", 79.289, 6.394, 6.489, 1, 0),
}


@pytest.fixture(scope="module")
def params() -> SavingsParams:
    """The savings assumptions as the pipeline reads them from config/assumptions.yaml."""
    return SavingsParams.from_config(config.load_assumptions())


@pytest.fixture(scope="module")
def mpg() -> float:
    """Local delivery mpg, from config."""
    return float(config.get(config.load_assumptions(), "fleet.local_mpg"))


@pytest.fixture(scope="module")
def card() -> pd.DataFrame:
    """The raw fuel card rows of the three fills, by transaction id."""
    frame = pd.read_csv(FUEL_CARD, dtype={"TransactionID": str})
    return frame[frame.TransactionID.isin(TXNS)].set_index("TransactionID")


@pytest.fixture(scope="module")
def missed_rows() -> dict:
    """The three fills as shown in site/data/missed.json (the confirmed-only list)."""
    rows = json.loads(MISSED.read_text(encoding="utf-8"))["rows"]
    return {r["txn_id"]: r for r in rows if r["txn_id"] in TXNS}


def fill_timestamp(row: pd.Series) -> pd.Timestamp:
    """The local time of a card fill, from its TransDate (MM/DD/YYYY) and TransTime (HH:MM)."""
    return pd.to_datetime(f"{row.TransDate} {row.TransTime}", format="%m/%d/%Y %H:%M")


def alt_posted_price(station_id: str, day: str) -> float:
    """The alternative's posted price that day (it is not a partner, so this is also its net price)."""
    prices = pd.read_csv(STATION_PRICES)
    hit = prices[(prices.station_id == station_id) & (prices.date == day)]
    assert len(hit) == 1
    return float(hit.posted_price_per_gal.iloc[0])


def worked(txn: str, card: pd.DataFrame, params: SavingsParams, mpg: float) -> dict:
    """Price one fill with the shared cost function: the price gap, both detour costs and the net saving."""
    case, row = CASES[txn], card.loc[txn]
    ts = fill_timestamp(row)
    factor = multiplier(ts, params)
    day = ts.strftime("%Y-%m-%d")
    paid, alt_price, gallons = float(row.PPG), alt_posted_price(case["alt_station"], day), float(row.Gallons)
    used = cost_parts(paid, *case["used"], mpg, params, factor)["total"]
    alt = cost_parts(alt_price, *case["alt"], mpg, params, factor)["total"]
    gap = (paid - alt_price) * gallons
    return {"ts": ts, "factor": factor, "paid": paid, "alt_price": alt_price, "gallons": gallons,
            "gap": gap, "used": used, "alt": alt, "net": gap + used - alt}


@pytest.mark.parametrize("txn", TXNS)
def test_inputs_match_the_committed_raw_files(txn, card):
    case, row = CASES[txn], card.loc[txn]
    assert row.Unit == case["truck"]
    assert fill_timestamp(row) == pd.Timestamp(case["timestamp"])
    _, _, alt_station, gallons, paid, alt_price, _, _ = DOC_INPUTS[txn]
    assert alt_station == case["alt_station"]
    assert float(row.Gallons) == gallons and float(row.PPG) == paid
    assert alt_posted_price(alt_station, fill_timestamp(row).strftime("%Y-%m-%d")) == alt_price


@pytest.mark.parametrize("txn", TXNS)
def test_route_stations_and_insertion_points_match_missed_json(txn, missed_rows):
    route, used_station, alt_station, _, _, _, used_after, alt_after = DOC_INPUTS[txn]
    row = missed_rows[txn]
    assert (row["route_id"], row["actual_station_id"], row["alt_station_id"]) == (route, used_station, alt_station)
    assert (row["actual_insert_after_stop"], row["alt_insert_after_stop"]) == (used_after, alt_after)


@pytest.mark.parametrize("txn", TXNS)
def test_full_precision_detours_match_the_hub(txn):
    """The typed-in detour inputs equal ref_detour in the built hub (skipped on a fresh clone with no hub)."""
    if not config.DB_PATH.is_file():
        pytest.skip("data/hub.duckdb not built here")
    import duckdb
    route, used_station, alt_station, _, _, _, used_after, alt_after = DOC_INPUTS[txn]
    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    try:
        def detour(station: str, after: int) -> tuple[float, float]:
            hit = con.execute("SELECT extra_miles, extra_minutes FROM ref_detour WHERE route_id = ? AND station_id = ? AND insert_after_stop = ?",
                              [route, station, after]).fetchall()
            assert len(hit) == 1
            return float(hit[0][0]), float(hit[0][1])
        assert detour(used_station, used_after) == pytest.approx(CASES[txn]["used"], abs=1e-9)
        assert detour(alt_station, alt_after) == pytest.approx(CASES[txn]["alt"], abs=1e-9)
    finally:
        con.close()


@pytest.mark.parametrize("txn", TXNS)
def test_rush_hour_factor_comes_from_multiplier_at_the_fill_time(txn, card, params):
    # 06:19 Thursday and 08:20 Thursday are inside the 6 to 9 am window (1.17); 05:14 Friday is before it (1.0).
    assert multiplier(fill_timestamp(card.loc[txn]), params) == CASES[txn]["factor"]


@pytest.mark.parametrize("txn", TXNS)
def test_each_part_and_the_net_match_the_hand_arithmetic(txn, card, params, mpg):
    case, got = CASES[txn], worked(txn, card, params, mpg)
    # To four decimals, the precision the doc works in.
    assert got["gap"] == pytest.approx(case["gap"], abs=5e-5)
    assert got["used"] == pytest.approx(case["used_cost"], abs=5e-5)
    assert got["alt"] == pytest.approx(case["alt_cost"], abs=5e-5)
    assert got["net"] == pytest.approx(case["net_exact"], abs=1.5e-4)
    # To the cent, each part rounded on its own.
    assert (round(got["gap"], 2), round(got["used"], 2), round(got["alt"], 2)) == case["cents"]
    assert round(got["net"], 2) == case["net"]


@pytest.mark.parametrize("txn", TXNS)
def test_missed_json_rows_show_the_hand_checked_figures(txn, card, params, mpg, missed_rows):
    case, got, row = CASES[txn], worked(txn, card, params, mpg), missed_rows[txn]
    assert row["net_savings_usd"] == case["net"]
    assert (row["price_gap_usd"], row["actual_detour_cost_usd"], row["alt_detour_cost_usd"]) == case["shown"]
    assert row["net_savings_usd"] == round(got["net"], 2)
    # Where the shown parts differ from their own roundings it is the alt detour, by one cent, balancing the sum (contract 11.5).
    assert [round(a - b, 2) for a, b in zip(case["shown"], case["cents"])] in ([0, 0, 0], [0, 0, 0.01], [0, 0, -0.01])
    assert (row["why"], row["why_line"]) == (case["why"], case["why_line"])
    assert round(row["price_gap_usd"] + row["actual_detour_cost_usd"] - row["alt_detour_cost_usd"], 2) == row["net_savings_usd"]
    assert row["multiplier"] == got["factor"] and row["is_peak"] is (got["factor"] > 1.0)
    # The rounded detour fields of the row are the full-precision inputs to 1 decimal (minutes) or 2 decimals (miles).
    assert row["actual_extra_miles"] == round(case["used"][0], 2) and row["alt_extra_miles"] == round(case["alt"][0], 2)
    assert row["actual_extra_minutes"] == pytest.approx(case["used"][1], abs=0.05)
    assert row["alt_extra_minutes"] == pytest.approx(case["alt"][1], abs=0.05)
    assert (row["gallons"], row["actual_price_per_gal"], row["alt_price_per_gal"]) == (got["gallons"], got["paid"], got["alt_price"])
    assert row["alt_station_id"] == case["alt_station"] and row["truck_id"] == case["truck"]

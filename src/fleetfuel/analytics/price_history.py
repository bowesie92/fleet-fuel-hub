"""Price history: what the fleet paid at each station each week, next to the EIA regional price.

In business terms: for every station and week we report the average price actually paid (net
of any partner discount, weighted by gallons), the average posted pump price, and the gallons
bought. The EIA weekly regional diesel average sits alongside as the market yardstick.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from fleetfuel import config


def week_start(day: pd.Series | pd.Timestamp):
    """Monday of the week containing a date (EIA weeks start on Monday)."""
    return (day - pd.to_timedelta(day.dt.day_of_week, unit="D")).dt.normalize() if isinstance(day, pd.Series) else (
        day - pd.Timedelta(days=day.weekday())
    ).normalize()


def period_weeks(cfg: dict) -> list[pd.Timestamp]:
    """Every Monday from the week holding the period start to the period end."""
    start = week_start(pd.Timestamp(config.get(cfg, "period.start_date")))
    end = pd.Timestamp(config.get(cfg, "period.end_date"))
    return list(pd.date_range(start, end, freq="7D"))


def eia_weekly(con: duckdb.DuckDBPyConnection, cfg: dict) -> pd.DataFrame:
    """EIA weekly diesel price for the weeks of the period."""
    eia = con.execute("SELECT week_start, price_per_gal FROM ref_eia_weekly ORDER BY week_start").df()
    eia["week_start"] = pd.to_datetime(eia["week_start"])
    weeks = period_weeks(cfg)
    return eia[(eia["week_start"] >= weeks[0]) & (eia["week_start"] <= weeks[-1])].reset_index(drop=True)


def station_weekly(con: duckdb.DuckDBPyConnection, cfg: dict) -> pd.DataFrame:
    """One row per station per week: gallon-weighted price paid (null with no fills), mean posted price, gallons."""
    weeks = pd.DataFrame({"week_start": period_weeks(cfg)})
    posted = con.execute("SELECT station_id, date, posted_price_per_gal FROM ref_station_price").df()
    posted["week_start"] = week_start(pd.to_datetime(posted["date"]))
    posted = posted.groupby(["station_id", "week_start"], as_index=False).agg(avg_posted_price=("posted_price_per_gal", "mean"))
    fills = con.execute("SELECT station_id, ts_local, gallons, price_per_gal FROM fact_fuel_txn WHERE station_id IS NOT NULL").df()
    fills["week_start"] = week_start(pd.to_datetime(fills["ts_local"]))
    fills["spend"] = fills["gallons"] * fills["price_per_gal"]
    paid = fills.groupby(["station_id", "week_start"], as_index=False).agg(spend=("spend", "sum"), gallons=("gallons", "sum"))
    paid["avg_price_paid"] = paid["spend"] / paid["gallons"]
    stations = con.execute("SELECT station_id FROM dim_station ORDER BY station_id").df()
    grid = stations.merge(weeks, how="cross")
    out = grid.merge(posted, on=["station_id", "week_start"], how="left").merge(
        paid[["station_id", "week_start", "avg_price_paid", "gallons"]], on=["station_id", "week_start"], how="left"
    )
    out["gallons"] = out["gallons"].fillna(0.0)
    return out.sort_values(["station_id", "week_start"]).reset_index(drop=True)


def station_totals(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Per station over the whole period: fills, gallons, gallon-weighted average price paid, mean posted price."""
    fills = con.execute(
        "SELECT station_id, count(*) AS fills, sum(gallons) AS gallons, sum(gallons * price_per_gal) AS spend "
        "FROM fact_fuel_txn WHERE station_id IS NOT NULL GROUP BY station_id"
    ).df()
    fills["avg_price_paid"] = fills["spend"] / fills["gallons"]
    posted = con.execute("SELECT station_id, avg(posted_price_per_gal) AS avg_posted_price FROM ref_station_price GROUP BY station_id").df()
    stations = con.execute("SELECT station_id FROM dim_station").df()
    out = stations.merge(fills[["station_id", "fills", "gallons", "avg_price_paid"]], on="station_id", how="left").merge(
        posted, on="station_id", how="left"
    )
    out["fills"] = out["fills"].fillna(0).astype(int)
    out["gallons"] = out["gallons"].fillna(0.0)
    return out.sort_values("station_id").reset_index(drop=True)

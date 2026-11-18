"""synth/params.py: read every assumption the generator needs from the config, once.

Business terms: this is the one place where the synthetic world's dials are collected,
so the rest of the generator never touches a raw config key or a magic number.
Keys the orchestrator has not added to config/assumptions.yaml yet fall back to the
named defaults below, and are listed in the synth-builder report as proposals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from fleetfuel.config import get

WEEKDAY_INDEX = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}



def _minutes(clock: str) -> int:
    """Turn 'HH:MM' into minutes since midnight."""
    hours, minutes = str(clock).split(":")
    return int(hours) * 60 + int(minutes)


@dataclass(frozen=True)
class Yard:
    """A fixed location the trucks start and end the day at."""

    id: str
    label: str
    lat: float
    lon: float
    onsite_fuel: bool  # True when the yard has its own bulk diesel tank


@dataclass(frozen=True)
class Params:
    """Every assumption the generator reads, with plain names."""

    seed: int
    start: date
    end: date
    tz: str
    operating_weekdays: frozenset[int]
    phx: Yard
    tuc: Yard
    linehaul_trucks: int
    local_trucks: int
    linehaul_tank_gal: float
    local_tank_gal: float
    linehaul_mpg: float
    local_mpg: float
    mpg_sd: float
    odo_min: int
    odo_max: int
    local_route_count: int
    stops_min: int
    stops_max: int
    minutes_per_stop: float
    speed_local: float
    speed_linehaul: float
    circuity: float
    local_start_min: int
    linehaul_start_min: int
    linehaul_runs: int
    linehaul_stagger_min: int
    dwell_min: float
    miles_noise: float
    refuel_below: float
    fill_to: float
    fill_sd: float
    search_radius_mi: float
    fill_minutes: float
    behavior_mix: tuple[tuple[str, float], ...]
    class_offsets: dict[str, float]
    station_offset_sd: float
    daily_noise_sd: float
    partner_bbox: tuple[float, float, float, float]  # south, west, north, east
    partner_named_only: bool
    partner_discount_cpg: float
    driver_cost_per_hour: float
    include_variable_cost: bool
    variable_cost_per_mile: float
    name_variant_mix: tuple[tuple[str, float], ...]
    duplicate_rate: float
    missing_odometer_rate: float
    wrong_odometer_rate: float
    ping_min: float
    ping_max: float
    gap_rate: float
    gap_min: float
    gap_max: float
    export_tz: str
    ping_noise_m: float
    ping_speed_sd: float
    odometer_min_error_mi: float
    use_unverified: bool
    mpg_cap_sd: float
    store_number_min: int
    store_number_max: int
    unnamed_label: str
    wrong_odometer_attempts: int
    wrong_odometer_fallback_multiple: float
    yard_discount_per_gal: float
    yard_skip_rate: float


def _yard(cfg: dict[str, Any], name: str) -> Yard:
    return Yard(
        id=get(cfg, f"yards.{name}.id"),
        label=get(cfg, f"yards.{name}.label"),
        lat=float(get(cfg, f"yards.{name}.lat")),
        lon=float(get(cfg, f"yards.{name}.lon")),
        onsite_fuel=bool(get(cfg, f"yards.{name}.onsite_fuel")),
    )


def _mix(cfg: dict[str, Any], key: str) -> tuple[tuple[str, float], ...]:
    """Read a share table and normalise it so the shares add to 1."""
    table = get(cfg, key)
    total = float(sum(table.values()))
    return tuple((name, float(share) / total) for name, share in table.items())


def load_params(cfg: dict[str, Any]) -> Params:
    """Collect every synth assumption from the config into one frozen object."""
    return Params(
        seed=int(get(cfg, "seed")),
        start=date.fromisoformat(str(get(cfg, "period.start_date"))),
        end=date.fromisoformat(str(get(cfg, "period.end_date"))),
        tz=get(cfg, "period.timezone"),
        operating_weekdays=frozenset(WEEKDAY_INDEX[d] for d in get(cfg, "period.operating_days")),
        phx=_yard(cfg, "phoenix_dc"),
        tuc=_yard(cfg, "tucson_yard"),
        linehaul_trucks=int(get(cfg, "fleet.linehaul_trucks")),
        local_trucks=int(get(cfg, "fleet.local_trucks")),
        linehaul_tank_gal=float(get(cfg, "fleet.linehaul_tank_gal")),
        local_tank_gal=float(get(cfg, "fleet.local_tank_gal")),
        linehaul_mpg=float(get(cfg, "fleet.linehaul_mpg")),
        local_mpg=float(get(cfg, "fleet.local_mpg")),
        mpg_sd=float(get(cfg, "fleet.mpg_daily_noise_sd")),
        odo_min=int(get(cfg, "fleet.starting_odometer_min_mi")),
        odo_max=int(get(cfg, "fleet.starting_odometer_max_mi")),
        local_route_count=int(get(cfg, "routes.local_route_count")),
        stops_min=int(get(cfg, "routes.stops_per_route_min")),
        stops_max=int(get(cfg, "routes.stops_per_route_max")),
        minutes_per_stop=float(get(cfg, "routes.minutes_per_stop")),
        speed_local=float(get(cfg, "routes.avg_speed_mph_local")),
        speed_linehaul=float(get(cfg, "routes.avg_speed_mph_linehaul")),
        circuity=float(get(cfg, "routes.straight_line_circuity")),
        local_start_min=_minutes(get(cfg, "routes.local_start_time")),
        linehaul_start_min=_minutes(get(cfg, "routes.linehaul_start_time")),
        linehaul_runs=int(get(cfg, "routes.linehaul_runs_per_day")),
        linehaul_stagger_min=int(get(cfg, "routes.linehaul_departure_stagger_minutes")),
        dwell_min=float(get(cfg, "routes.linehaul_yard_dwell_minutes")),
        miles_noise=float(get(cfg, "routes.actual_miles_noise_pct")),
        refuel_below=float(get(cfg, "fueling.refuel_below_tank_fraction")),
        fill_to=float(get(cfg, "fueling.fill_to_tank_fraction")),
        fill_sd=float(get(cfg, "fueling.fill_fraction_noise_sd")),
        search_radius_mi=float(get(cfg, "fueling.search_radius_mi")),
        fill_minutes=float(get(cfg, "fueling.fill_minutes")),
        behavior_mix=_mix(cfg, "fueling.behavior_mix"),
        class_offsets={k: float(v) for k, v in get(cfg, "prices.class_offset_per_gal").items()},
        station_offset_sd=float(get(cfg, "prices.station_offset_sd_per_gal")),
        daily_noise_sd=float(get(cfg, "prices.daily_noise_sd_per_gal")),
        partner_bbox=tuple(float(v) for v in get(cfg, "prices.partner_set_bbox")),  # type: ignore[arg-type]
        partner_named_only=bool(get(cfg, "prices.partner_set_named_only")),
        partner_discount_cpg=float(get(cfg, "prices.partner_discount_cpg")),
        driver_cost_per_hour=float(get(cfg, "savings.driver_cost_per_hour")),
        include_variable_cost=bool(get(cfg, "savings.include_variable_cost")),
        variable_cost_per_mile=float(get(cfg, "savings.variable_cost_per_mile")),
        name_variant_mix=_mix(cfg, "messiness.name_variant_mix"),
        duplicate_rate=float(get(cfg, "messiness.duplicate_swipe_rate")),
        missing_odometer_rate=float(get(cfg, "messiness.missing_odometer_rate")),
        wrong_odometer_rate=float(get(cfg, "messiness.wrong_odometer_rate")),
        ping_min=float(get(cfg, "messiness.ping_interval_min_minutes")),
        ping_max=float(get(cfg, "messiness.ping_interval_max_minutes")),
        gap_rate=float(get(cfg, "messiness.gps_gap_rate_per_truck_day")),
        gap_min=float(get(cfg, "messiness.gps_gap_minutes_min")),
        gap_max=float(get(cfg, "messiness.gps_gap_minutes_max")),
        export_tz=get(cfg, "messiness.telematics_export_timezone"),
        ping_noise_m=float(get(cfg, "messiness.ping_position_noise_m")),
        ping_speed_sd=float(get(cfg, "messiness.ping_speed_sd_fraction")),
        use_unverified=bool(get(cfg, "fueling.use_unverified_truck_access")),
        odometer_min_error_mi=float(get(cfg, "pipeline.odometer.max_gap_vs_telematics_mi")),
        mpg_cap_sd=float(get(cfg, "fleet.mpg_noise_cap_sd")),
        store_number_min=int(get(cfg, "messiness.store_number_min")),
        store_number_max=int(get(cfg, "messiness.store_number_max")),
        unnamed_label=str(get(cfg, "messiness.unnamed_station_merchant_label")),
        wrong_odometer_attempts=int(get(cfg, "messiness.wrong_odometer_attempts")),
        wrong_odometer_fallback_multiple=float(get(cfg, "messiness.wrong_odometer_fallback_multiple")),
        yard_discount_per_gal=float(get(cfg, "yard_fuel.discount_vs_eia_per_gal")),
        yard_skip_rate=float(get(cfg, "yard_fuel.skip_rate")),
    )

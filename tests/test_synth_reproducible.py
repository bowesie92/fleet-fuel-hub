"""Two runs with the same config must give byte-identical files; a different seed must not."""

from __future__ import annotations

from pathlib import Path

from test_synth_common import _TMP, OVERRIDES, tiny_cfg

from fleetfuel import synth

OUTPUT_FILES = [
    "fleet.csv", "partner_program.csv", "station_prices.csv", "fuel_card.csv", "telematics.csv",
    "route_plans.csv", "yard_fuel.csv", "ground_truth/fuel_txn_truth.csv", "ground_truth/injected_issues.csv",
    "ground_truth/run_manifest.json",
]


def _run(name: str, cfg: dict) -> Path:
    out = Path(_TMP.name) / name
    synth.run(cfg, raw_dir=Path(_TMP.name) / "no_real_inputs", out_dir=out, overrides_path=OVERRIDES)
    return out


def test_same_config_gives_byte_identical_files():
    a, b = _run("repro_a", tiny_cfg()), _run("repro_b", tiny_cfg())
    for name in OUTPUT_FILES:
        assert (a / name).read_bytes() == (b / name).read_bytes(), name


def test_different_seed_changes_the_data():
    other = tiny_cfg()
    other["seed"] += 1
    a, c = _run("repro_a", tiny_cfg()), _run("repro_c", other)
    assert (a / "fuel_card.csv").read_bytes() != (c / "fuel_card.csv").read_bytes()


def test_files_use_lf_line_endings_and_utf8():
    out = _run("repro_a", tiny_cfg())
    for name in OUTPUT_FILES:
        raw = (out / name).read_bytes()
        assert b"\r" not in raw, name
        raw.decode("utf-8")

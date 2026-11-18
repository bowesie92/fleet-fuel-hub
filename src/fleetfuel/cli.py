"""Command line entry point: `uv run fleetfuel fetch | synth | build`.

Each command calls a fixed `run()` function in one subpackage (see docs/contracts.md
section 8). Builders fill in their own `run()`; this file does not change when they do.
"""

from __future__ import annotations

import argparse
import sys

from fleetfuel import config

BUILD_STEPS = ["ingest", "normalize", "routing", "analytics", "export"]


def cmd_fetch(cfg: dict, refresh: bool) -> None:
    """Fetch the real public inputs (EIA prices, OSM stations, customer points), cache first."""
    from fleetfuel import fetch

    fetch.run(cfg, refresh=refresh)


def cmd_synth(cfg: dict) -> None:
    """Generate the synthetic fleet, operations and messy source files."""
    from fleetfuel import synth

    synth.run(cfg)


def cmd_build(cfg: dict) -> None:
    """Run the whole pipeline from data/raw and the cache: load the hub, analyze, export the site data."""
    import importlib

    for step in BUILD_STEPS:
        print(f"[build] {step}")
        importlib.import_module(f"fleetfuel.{step}").run(cfg)


def make_parser() -> argparse.ArgumentParser:
    """Describe the three subcommands."""
    parser = argparse.ArgumentParser(prog="fleetfuel", description="Fleet Fuel Hub pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch_p = sub.add_parser("fetch", help="fetch real public data (needs keys only on a cache miss)")
    fetch_p.add_argument("--refresh", action="store_true", help="ignore the cache and call the APIs")
    sub.add_parser("synth", help="generate synthetic data into data/raw/")
    sub.add_parser("build", help="run the full pipeline from data/raw and the cache")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the chosen command."""
    args = make_parser().parse_args(argv)
    cfg = config.load_assumptions()
    if args.command == "fetch":
        cmd_fetch(cfg, args.refresh)
    elif args.command == "synth":
        cmd_synth(cfg)
    elif args.command == "build":
        cmd_build(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Smoke tests: the package imports, the CLI parses, and every stage has a run() entry point."""

import importlib

import pytest

from fleetfuel import cli


def test_package_imports():
    import fleetfuel

    assert fleetfuel.__version__


@pytest.mark.parametrize("step", ["fetch", "synth", *cli.BUILD_STEPS])
def test_every_stage_has_run(step):
    module = importlib.import_module(f"fleetfuel.{step}")
    assert callable(module.run)


@pytest.mark.parametrize("command", ["fetch", "synth", "build"])
def test_cli_knows_each_command(command):
    args = cli.make_parser().parse_args([command])
    assert args.command == command

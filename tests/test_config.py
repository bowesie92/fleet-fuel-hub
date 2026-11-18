"""The assumptions file loads, every value has a one-line note, and get() fails loudly."""

import pytest

from fleetfuel import config


def test_assumptions_load():
    cfg = config.load_assumptions()
    assert cfg["seed"] == config.get(cfg, "seed")


def test_every_assumption_has_a_note():
    rows = config.describe_assumptions()
    assert len(rows) > 50
    missing = [r["key"] for r in rows if not r["note"]]
    assert missing == []


def test_describe_matches_loaded_values():
    cfg = config.load_assumptions()
    for row in config.describe_assumptions():
        assert config.get(cfg, row["key"]) == row["value"], row["key"]


def test_missing_key_raises_with_name():
    with pytest.raises(KeyError, match="savings.no_such_key"):
        config.get(config.load_assumptions(), "savings.no_such_key")


def test_behavior_and_name_mixes_sum_to_one():
    cfg = config.load_assumptions()
    for key in ["fueling.behavior_mix", "messiness.name_variant_mix"]:
        assert sum(config.get(cfg, key).values()) == pytest.approx(1.0), key

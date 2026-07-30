"""Tests for config."""

import tempfile
from pathlib import Path
from kirox.utils.config import Config, load_config


def test_config_defaults():
    assert Config().region == "us-east-1" and Config().server_port == 8420


def test_config_from_file():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "config.json"
        Config(region="eu-west-1").to_file(p)
        assert Config.from_file(p).region == "eu-west-1"


def test_load_config_env(monkeypatch):
    monkeypatch.setenv("KURO_TOKEN", "tok")
    assert load_config(Path("/nonexistent")).token == "tok"

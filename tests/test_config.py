"""Tests for validated, atomically persisted configuration."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from kirox.core.client import _default_runtime_url
from kirox.utils.config import (
    Config,
    ConfigError,
    default_config_path,
    get_config_path,
    load_config,
)


def test_config_defaults() -> None:
    config = Config()
    assert config.region == "us-east-1"
    assert config.server_port == 8420
    assert config.server_host == "127.0.0.1"
    assert config.log_level == "INFO"


def test_config_round_trips_every_field_through_a_file(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    original = Config(
        region="eu-west-1",
        server_port=0,
        server_host="::1",
        auto_refresh=False,
        refresh_interval=60,
        log_level="DEBUG",
        log_file=str(tmp_path / "kirox.log"),
        token="secret-token",
        profile_arn="arn:test",
        db_path=str(tmp_path / "data.sqlite3"),
    )

    original.to_file(path)

    assert Config.from_file(path) == original
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {
        "region",
        "server_port",
        "server_host",
        "auto_refresh",
        "refresh_interval",
        "log_level",
        "log_file",
        "token",
        "profile_arn",
        "db_path",
    }


def test_config_file_uses_utf8_regardless_of_platform_locale(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    log_file = str(tmp_path / "catatan-層-日志.log")

    Config(log_file=log_file).to_file(path)

    # Written as literal UTF-8, so a locale-dependent read would corrupt it.
    assert "層" in path.read_bytes().decode("utf-8")
    assert Config.from_file(path).log_file == log_file


def test_config_is_written_atomically_and_privately(tmp_path: Path) -> None:
    path = tmp_path / "config.json"

    Config(token="secret-token").to_file(path)

    assert list(tmp_path.iterdir()) == [path]
    if os.name == "posix":
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0


def test_failed_write_leaves_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    monkeypatch.setattr(os, "replace", lambda *args: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(OSError, match="disk full"):
        Config().to_file(path)

    assert list(tmp_path.iterdir()) == []


def test_missing_file_falls_back_to_defaults(tmp_path: Path) -> None:
    assert Config.from_file(tmp_path / "absent.json") == Config()


def test_unknown_keys_are_ignored_for_forward_compatibility() -> None:
    config = Config.from_dict({"region": "eu-west-1", "future_option": True})

    assert config.region == "eu-west-1"
    assert not hasattr(config, "future_option")


def test_log_level_is_normalized() -> None:
    assert Config(log_level="debug").log_level == "DEBUG"
    assert Config(log_level=" info ").log_level == "INFO"


@pytest.mark.parametrize(
    ("given", "expected"),
    [("WARN", "WARNING"), ("warn", "WARNING"), ("FATAL", "CRITICAL"), ("fatal", "CRITICAL")],
)
def test_legacy_log_level_aliases_still_load(given: str, expected: str) -> None:
    # Earlier versions resolved levels with getattr(logging, name), so these
    # names worked and must not become a hard startup failure.
    assert Config(log_level=given).log_level == expected


def test_string_fields_are_stripped_so_they_cannot_corrupt_urls() -> None:
    config = Config(region=" us-east-1 ")

    assert config.region == "us-east-1"
    assert _default_runtime_url(config.region) == "https://codewhisperer.us-east-1.amazonaws.com"


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("region", ""),
        ("region", None),
        ("region", 1),
        ("server_port", "8420"),
        ("server_port", True),
        ("server_port", 65536),
        ("server_port", -1),
        ("server_host", "0.0.0.0"),
        ("server_host", "example.com"),
        ("server_host", ""),
        ("auto_refresh", "yes"),
        ("refresh_interval", 0),
        ("refresh_interval", -1),
        ("refresh_interval", 1.5),
        ("log_level", "LOUD"),
        ("log_file", 5),
        ("token", ""),
        ("profile_arn", 0),
        ("db_path", []),
    ],
)
def test_invalid_values_are_rejected_with_the_field_name(field_name: str, value: Any) -> None:
    with pytest.raises(ConfigError) as error_info:
        Config(**{field_name: value})

    assert error_info.value.field_name == field_name
    assert field_name in str(error_info.value)


def test_invalid_file_content_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"server_host": "0.0.0.0"}), encoding="utf-8")

    with pytest.raises(ConfigError, match="server_host"):
        Config.from_file(path)


def test_non_object_file_content_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ConfigError, match="body"):
        Config.from_file(path)


def test_malformed_json_is_reported_as_a_config_error(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"region": "us-east-1"', encoding="utf-8")

    with pytest.raises(ConfigError, match="must be valid JSON") as error_info:
        Config.from_file(path)

    assert error_info.value.field_name == "body"


def test_non_utf8_file_is_reported_as_a_config_error(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_bytes(b'{"log_file": "caf\xe9.log"}')

    with pytest.raises(ConfigError, match="must be UTF-8 encoded"):
        Config.from_file(path)


def test_every_load_failure_is_a_single_catchable_type(tmp_path: Path) -> None:
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{", encoding="utf-8")
    bad_field = tmp_path / "field.json"
    bad_field.write_text(json.dumps({"server_port": "8420"}), encoding="utf-8")
    bad_encoding = tmp_path / "encoding.json"
    bad_encoding.write_bytes(b'{"region": "\xff"}')

    for path in (bad_json, bad_field, bad_encoding):
        with pytest.raises(ConfigError):
            load_config(path)


def test_stale_temporary_files_are_swept_on_the_next_write(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    stale = tmp_path / ".config.json.abcdef.tmp"
    stale.write_text('{"token": "leaked-from-a-crashed-write"}', encoding="utf-8")

    Config().to_file(path)

    assert not stale.exists()
    assert list(tmp_path.iterdir()) == [path]


def test_get_config_path_precedence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("KIROX_CONFIG", raising=False)
    assert get_config_path() == default_config_path()

    monkeypatch.setenv("KIROX_CONFIG", str(tmp_path / "override.json"))
    assert get_config_path() == tmp_path / "override.json"
    assert get_config_path(tmp_path / "explicit.json") == tmp_path / "explicit.json"


def test_default_path_follows_a_relocated_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("KIROX_CONFIG", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert default_config_path() == tmp_path / ".kirox" / "config.json"
    assert get_config_path() == tmp_path / ".kirox" / "config.json"


def test_kirox_config_override_is_loaded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "override.json"
    path.write_text(json.dumps({"region": "ap-southeast-1"}), encoding="utf-8")
    monkeypatch.setenv("KIROX_CONFIG", str(path))

    assert load_config().region == "ap-southeast-1"


def test_environment_overlays_every_supported_field(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("KIROX_TOKEN", "env-token")
    monkeypatch.setenv("KIROX_PROFILE_ARN", "env-profile")
    monkeypatch.setenv("KIROX_REGION", "eu-west-1")
    monkeypatch.setenv("KIROX_SERVER_HOST", "::1")
    monkeypatch.setenv("KIROX_SERVER_PORT", "9001")
    monkeypatch.setenv("KIROX_LOG_LEVEL", "debug")
    monkeypatch.setenv("KIROX_LOG_FILE", str(tmp_path / "kirox.log"))

    config = load_config(tmp_path / "absent.json")

    assert config.token == "env-token"
    assert config.profile_arn == "env-profile"
    assert config.region == "eu-west-1"
    assert config.server_host == "::1"
    assert config.server_port == 9001
    assert config.log_level == "DEBUG"
    assert config.log_file == str(tmp_path / "kirox.log")


def test_environment_overlays_are_validated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("KIROX_SERVER_HOST", "0.0.0.0")

    with pytest.raises(ConfigError, match="server_host"):
        load_config(tmp_path / "absent.json")


@pytest.mark.parametrize("raw_port", ["not-a-port", "8420.5", "-1", "٩٩"])
def test_non_integer_port_override_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, raw_port: str
) -> None:
    monkeypatch.setenv("KIROX_SERVER_PORT", raw_port)

    with pytest.raises(ConfigError, match="server_port"):
        load_config(tmp_path / "absent.json")


def test_out_of_range_port_override_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("KIROX_SERVER_PORT", "70000")

    with pytest.raises(ConfigError, match="server_port"):
        load_config(tmp_path / "absent.json")


def test_empty_environment_values_do_not_overlay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"region": "eu-west-1"}), encoding="utf-8")
    for variable in (
        "KIROX_TOKEN",
        "KIROX_REGION",
        "KIROX_SERVER_HOST",
        "KIROX_SERVER_PORT",
        "KIROX_LOG_LEVEL",
    ):
        monkeypatch.setenv(variable, "")

    config = load_config(path)

    assert config == Config(region="eu-west-1")


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX permission semantics")
def test_written_config_is_not_group_or_world_readable(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    Config(token="secret-token").to_file(path)

    assert stat.S_IMODE(path.stat().st_mode) == stat.S_IRUSR | stat.S_IWUSR

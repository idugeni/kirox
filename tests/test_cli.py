"""Tests for lifecycle-aware CLI behavior."""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

import kirox.cli as cli
import kirox.service.daemon as daemon_module
import kirox.service.state as state_module
import kirox.utils.config as config_module
import kirox.utils.logging as logging_module
from kirox.service.process_identity import ProcessIdentity
from kirox.service.server import CONTROL_SHUTDOWN_PATH, CONTROL_TOKEN_HEADER
from kirox.service.state import ServiceState


def _state(*, with_identity: bool = True) -> ServiceState:
    return ServiceState(
        pid=4321,
        url="http://127.0.0.1:8420",
        started_at=123.0,
        control_token="control-token",
        process_identity=(
            ProcessIdentity("windows-creation-time", ("12345",)) if with_identity else None
        ),
    )


def test_parser_ask():
    args = cli.create_parser().parse_args(["ask", "-m", "test", "hello"])
    assert args.model == "test" and args.message == "hello"


def test_parser_status():
    assert cli.create_parser().parse_args(["status"]).command == "status"


def test_parser_models():
    assert cli.create_parser().parse_args(["models"]).command == "models"


def test_parser_update():
    args = cli.create_parser().parse_args(["update", "-y"])
    assert args.command == "update" and args.yes is True


def test_run_with_no_tray():
    args = cli.create_parser().parse_args(["run", "--no-tray", "--no-update"])
    assert args.no_tray is True and args.no_update is True


def test_bare_invocation_matches_explicit_run_defaults():
    bare = vars(cli.create_parser().parse_args([]))
    explicit = vars(cli.create_parser().parse_args(["run"]))

    assert bare == explicit
    assert bare["command"] == "run"
    for name, default in cli.RUN_DEFAULTS.items():
        assert bare[name] is default


def test_stop_force_flag_is_additive():
    args = cli.create_parser().parse_args(["stop", "--force"])
    assert args.command == "stop" and args.force is True


@pytest.mark.parametrize(
    ("candidate", "current", "expected"),
    [
        ("1.2.0", "1.1.0", True),
        ("1.1.0", "1.2.0", False),
        ("1.2.0", "1.2.0", False),
        ("1.2.1", "1.2.0", True),
        ("1.10.0", "1.9.0", True),
        ("2.0", "1.99.99", True),
        ("1.2", "1.2.0", False),
        ("1.2.0.1", "1.2.0", True),
        ("1.2.0+local", "1.2.0", False),
        ("not-a-version", "1.2.0", False),
        ("1.2.0", "not-a-version", False),
    ],
)
def test_is_newer_version(candidate, current, expected):
    assert cli._is_newer_version(candidate, current) is expected


def test_check_update_ignores_an_index_behind_the_installed_build(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_should_check_update", lambda: True)
    monkeypatch.setattr(cli, "_update_cache", lambda: None)
    monkeypatch.setattr(cli, "_get_latest_version", lambda: "1.0.0")
    monkeypatch.setattr(cli, "__version__", "1.2.0")

    assert cli._check_update() is None
    assert "Update available" not in capsys.readouterr().out


def test_check_update_reports_a_newer_index_version(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_should_check_update", lambda: True)
    monkeypatch.setattr(cli, "_update_cache", lambda: None)
    monkeypatch.setattr(cli, "_get_latest_version", lambda: "1.3.0")
    monkeypatch.setattr(cli, "__version__", "1.2.0")

    assert cli._check_update() == "1.3.0"
    assert "Update available: 1.2.0 -> 1.3.0" in capsys.readouterr().out


def test_check_update_stays_silent_when_asked(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_should_check_update", lambda: True)
    monkeypatch.setattr(cli, "_update_cache", lambda: None)
    monkeypatch.setattr(cli, "_get_latest_version", lambda: "1.3.0")
    monkeypatch.setattr(cli, "__version__", "1.2.0")

    assert cli._check_update(silent=True) == "1.3.0"
    assert capsys.readouterr().out == ""


def test_status_reads_state_and_api_token_status(monkeypatch, capsys):
    service_state = _state()
    health = Mock(status_code=200)
    token_status = Mock(
        status_code=200,
        json=Mock(return_value={"authenticated": True, "has_profile": False}),
    )
    get = Mock(side_effect=[health, token_status])
    monkeypatch.setattr(state_module, "read_state", lambda: service_state)
    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(cli, "_check_update", lambda silent=False: None)

    cli.cmd_status(argparse.Namespace())

    output = capsys.readouterr().out
    assert "Status:   RUNNING" in output
    assert "Auth:     OK" in output
    assert "Profile:  NO" in output
    assert get.call_args_list[1].args[0] == f"{service_state.url}/api/token/status"


def test_stop_uses_control_token_then_polls(monkeypatch):
    service_state = _state()
    response = Mock(status_code=200)
    post = Mock(return_value=response)
    wait = Mock(return_value=True)
    monkeypatch.setattr(state_module, "read_state", lambda: service_state)
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(cli, "_wait_until_stopped", wait)

    result = cli.cmd_stop(argparse.Namespace(force=False))

    assert result == 0
    assert post.call_args.args[0] == f"{service_state.url}{CONTROL_SHUTDOWN_PATH}"
    assert post.call_args.kwargs["headers"] == {CONTROL_TOKEN_HEADER: service_state.control_token}
    wait.assert_called_once_with(service_state, cli.STOP_TIMEOUT)


def test_force_stop_targets_only_validated_state_pid(monkeypatch):
    service_state = _state()
    force = Mock()
    monkeypatch.setattr(state_module, "read_state", lambda: service_state)
    monkeypatch.setattr(httpx, "post", Mock(return_value=Mock(status_code=403)))
    monkeypatch.setattr(cli, "_force_stop_pid", force)
    monkeypatch.setattr(cli, "_wait_until_stopped", Mock(return_value=True))

    result = cli.cmd_stop(argparse.Namespace(force=True))

    assert result == 0
    force.assert_called_once_with(service_state.pid, service_state.process_identity)


def test_force_stop_refuses_legacy_state_after_graceful_attempt(monkeypatch, capsys):
    service_state = _state(with_identity=False)
    post = Mock(return_value=Mock(status_code=403))
    force = Mock()
    monkeypatch.setattr(state_module, "read_state", lambda: service_state)
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(cli, "_force_stop_pid", force)
    monkeypatch.setattr(cli, "_wait_until_stopped", Mock(return_value=False))

    result = cli.cmd_stop(argparse.Namespace(force=True))

    assert result == 1
    assert post.call_args.kwargs["headers"] == {CONTROL_TOKEN_HEADER: service_state.control_token}
    force.assert_not_called()
    assert "no verifiable process identity" in capsys.readouterr().err


def test_run_without_tray_waits_and_uses_logging_config(monkeypatch, tmp_path):
    events: list[str] = []

    class FakeService:
        url = "http://127.0.0.1:12345"

        def __init__(self, config) -> None:
            self.config = config

        def start(self) -> None:
            events.append("start")

        def wait(self) -> None:
            events.append("wait")

        def stop(self) -> None:
            events.append("stop")

    config = SimpleNamespace(log_level="WARNING", log_file=str(tmp_path / "kirox.log"))
    setup_logging = Mock()
    monkeypatch.setattr(config_module, "load_config", lambda: config)
    monkeypatch.setattr(logging_module, "setup_logging", setup_logging)
    monkeypatch.setattr(daemon_module, "KiroxService", FakeService)
    monkeypatch.setattr(cli.signal, "getsignal", lambda signum: None)
    monkeypatch.setattr(cli.signal, "signal", Mock())

    cli.cmd_run(argparse.Namespace(no_update=True, no_tray=True, verbose=False))

    assert events == ["start", "wait", "stop"]
    setup_logging.assert_called_once_with(
        level="WARNING",
        log_file=Path(config.log_file),
    )

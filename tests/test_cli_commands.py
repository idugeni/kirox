"""Network-free tests for CLI command behavior and failure paths."""

from __future__ import annotations

import argparse
import io
import signal
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import httpx
import pytest

import kirox.cli as cli
import kirox.service.daemon as daemon_module
import kirox.service.process_identity as process_identity_module
import kirox.service.state as state_module
import kirox.service.tray as tray_module
import kirox.utils.config as config_module
import kirox.utils.logging as logging_module
from kirox.core.client import AssistantClient
from kirox.core.models import StreamEvent
from kirox.service.process_identity import ProcessIdentity
from kirox.service.state import ServiceState


def service_state(pid: int = 4321) -> ServiceState:
    return ServiceState(
        pid=pid,
        url="http://127.0.0.1:8420",
        started_at=123.0,
        control_token="control-token",
        process_identity=ProcessIdentity("linux-proc-start", ("boot-id", "12345")),
    )


def test_latest_version_and_update_cache_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    response = Mock(status_code=200)
    response.json.return_value = {"info": {"version": "2.0.0"}}
    get = Mock(side_effect=[response, Mock(status_code=503), httpx.ConnectError("offline")])
    monkeypatch.setattr(httpx, "get", get)

    assert cli._get_latest_version() == "2.0.0"
    assert cli._get_latest_version() is None
    assert cli._get_latest_version() is None

    cache = tmp_path / "update-cache"
    monkeypatch.setattr(cli, "UPDATE_CACHE_FILE", cache)
    monkeypatch.setattr(cli, "UPDATE_CHECK_INTERVAL", 100)
    monkeypatch.setattr(cli.time, "time", lambda: 1000.0)
    assert cli._should_check_update()

    cache.write_text("950", encoding="utf-8")
    assert not cli._should_check_update()
    cache.write_text("invalid", encoding="utf-8")
    assert cli._should_check_update()

    monkeypatch.setattr(cli, "_get_latest_version", lambda: "2.0.0")
    assert cli._check_update() == "2.0.0"
    assert cache.read_text(encoding="utf-8") == "1000.0"
    assert "Update available" in capsys.readouterr().out
    assert cli._check_update() is None


def test_do_update_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run = Mock()
    monkeypatch.setattr(subprocess, "run", run)

    cli._do_update()
    run.assert_called_once_with(
        [cli.sys.executable, "-m", "pip", "install", "--upgrade", "kirox"],
        check=True,
    )
    assert "Update complete" in capsys.readouterr().out

    run.side_effect = subprocess.CalledProcessError(1, "pip")
    cli._do_update()
    assert "Update failed" in capsys.readouterr().out


def test_update_command_current_cancel_and_accept(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    latest = iter([cli.__version__, "2.0.0", "2.0.0"])
    monkeypatch.setattr(cli, "_get_latest_version", lambda: next(latest))
    answers = iter(["n"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    update = Mock()
    monkeypatch.setattr(cli, "_do_update", update)

    cli.cmd_update(argparse.Namespace(yes=False))
    cli.cmd_update(argparse.Namespace(yes=False))
    cli.cmd_update(argparse.Namespace(yes=True))

    output = capsys.readouterr().out
    assert "Already up to date" in output
    assert "Cancelled" in output
    update.assert_called_once_with()


def test_status_handles_stopped_unhealthy_and_transport_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = service_state()
    states: list[ServiceState | None] = [None, state, state]
    monkeypatch.setattr(state_module, "read_state", lambda: states.pop(0))
    monkeypatch.setattr(cli, "_check_update", lambda silent=False: "2.0.0")
    get = Mock(side_effect=[Mock(status_code=503), httpx.ConnectError("offline")])
    monkeypatch.setattr(httpx, "get", get)

    cli.cmd_status(argparse.Namespace())
    cli.cmd_status(argparse.Namespace())
    cli.cmd_status(argparse.Namespace())

    output = capsys.readouterr().out
    assert "Update available: 2.0.0" in output
    assert output.count("Status:   STOPPED") == 2
    assert "Status:   ERROR" in output


def test_wait_until_stopped_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    state = service_state()
    monkeypatch.setattr(state_module, "read_state", lambda: None)
    assert cli._wait_until_stopped(state, 1)

    monkeypatch.setattr(state_module, "read_state", lambda: state)
    monkeypatch.setattr(httpx, "get", Mock(side_effect=httpx.ConnectError("offline")))
    monkeypatch.setattr(
        process_identity_module,
        "capture_process_identity",
        Mock(side_effect=ProcessLookupError()),
    )
    clear = Mock()
    monkeypatch.setattr(state_module, "clear_state", clear)
    assert cli._wait_until_stopped(state, 1)
    clear.assert_called_once_with(state)

    monkeypatch.setattr(httpx, "get", Mock(return_value=Mock(status_code=200)))
    assert not cli._wait_until_stopped(state, 0)


def test_wait_treats_reused_pid_as_stopped_without_signalling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = service_state()
    monkeypatch.setattr(state_module, "read_state", lambda: state)
    monkeypatch.setattr(httpx, "get", Mock(side_effect=httpx.ConnectError("offline")))
    monkeypatch.setattr(
        process_identity_module,
        "capture_process_identity",
        lambda pid: ProcessIdentity("linux-proc-start", ("boot-id", "99999")),
    )
    clear = Mock()
    monkeypatch.setattr(state_module, "clear_state", clear)

    assert cli._wait_until_stopped(state, 1)
    clear.assert_called_once_with(state)


def test_force_stop_rejects_invalid_and_current_process() -> None:
    identity = ProcessIdentity("linux-proc-start", ("boot-id", "12345"))
    with pytest.raises(ValueError, match="invalid PID"):
        cli._force_stop_pid(0, identity)
    with pytest.raises(ValueError, match="invalid PID"):
        cli._force_stop_pid(True, identity)
    with pytest.raises(RuntimeError, match="current process"):
        cli._force_stop_pid(cli.os.getpid(), identity)


def test_stop_command_failure_and_ownership_paths(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = service_state()
    monkeypatch.setattr(httpx, "post", Mock(return_value=Mock(status_code=403)))
    monkeypatch.setattr(cli, "_wait_until_stopped", Mock(return_value=False))

    monkeypatch.setattr(state_module, "read_state", lambda: None)
    assert cli.cmd_stop(argparse.Namespace(force=False)) == 0

    monkeypatch.setattr(state_module, "read_state", lambda: state)
    assert cli.cmd_stop(argparse.Namespace(force=False)) == 1

    replacement = service_state(pid=9999)
    states = iter([state, replacement])
    monkeypatch.setattr(state_module, "read_state", lambda: next(states))
    assert cli.cmd_stop(argparse.Namespace(force=True)) == 1

    captured = capsys.readouterr()
    assert "not running" in captured.out
    assert "Graceful stop failed" in captured.err
    assert "ownership changed" in captured.err


def test_stop_command_force_error_and_missing_process_paths(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = service_state()
    monkeypatch.setattr(httpx, "post", Mock(return_value=Mock(status_code=403)))
    monkeypatch.setattr(cli, "_wait_until_stopped", Mock(return_value=False))
    monkeypatch.setattr(state_module, "read_state", lambda: state)
    clear = Mock()
    monkeypatch.setattr(state_module, "clear_state", clear)

    monkeypatch.setattr(cli, "_force_stop_pid", Mock(side_effect=ProcessLookupError()))
    assert cli.cmd_stop(argparse.Namespace(force=True)) == 0
    clear.assert_called_once_with(state)

    monkeypatch.setattr(cli, "_force_stop_pid", Mock(side_effect=OSError("denied")))
    assert cli.cmd_stop(argparse.Namespace(force=True)) == 1
    assert "Force stop failed" in capsys.readouterr().err

    monkeypatch.setattr(cli, "_force_stop_pid", Mock())
    assert cli.cmd_stop(argparse.Namespace(force=True)) == 1
    assert "timed out" in capsys.readouterr().err


def test_stop_treats_transport_close_as_possible_graceful_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = service_state()
    monkeypatch.setattr(state_module, "read_state", lambda: state)
    monkeypatch.setattr(httpx, "post", Mock(side_effect=httpx.ConnectError("closed")))
    wait = Mock(return_value=True)
    monkeypatch.setattr(cli, "_wait_until_stopped", wait)

    assert cli.cmd_stop(argparse.Namespace(force=False)) == 0
    wait.assert_called_once_with(state, cli.STOP_TIMEOUT)


class FakeCLIClient:
    def __init__(self) -> None:
        self.close_calls = 0
        self.simple_calls: list[tuple[str, str]] = []
        self.chat_calls: list[tuple[str, str]] = []

    def list_models(self) -> list[Any]:
        return [
            SimpleNamespace(
                model_id="model-one",
                model_name="Model One",
                rate_multiplier=1.5,
                supports_thinking=True,
            )
        ]

    def chat(self, message: str, *, model_id: str = "auto") -> Any:
        self.chat_calls.append((message, model_id))
        return iter(
            [
                StreamEvent(event_type="content", content="answer"),
                StreamEvent(event_type="end", done=True),
            ]
        )

    def chat_simple(self, message: str, model_id: str = "auto") -> str:
        self.simple_calls.append((message, model_id))
        return "simple-answer"

    def close(self) -> None:
        self.close_calls += 1


def test_models_chat_and_ask_commands_close_clients(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeCLIClient()
    monkeypatch.setattr(
        AssistantClient,
        "auto",
        classmethod(lambda cls: cast(AssistantClient, fake)),
    )

    cli.cmd_models(argparse.Namespace())
    messages = iter(["hello", "quit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(messages))
    cli.cmd_chat(argparse.Namespace(model="model-one"))
    cli.cmd_ask(argparse.Namespace(message="question", model="model-two"))
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("stdin question"))
    cli.cmd_ask(argparse.Namespace(message=None, model="auto"))

    output = capsys.readouterr().out
    assert "model-one" in output
    assert "answer" in output
    assert output.count("simple-answer") == 2
    assert fake.chat_calls == [("hello", "model-one")]
    assert fake.simple_calls == [("question", "model-two"), ("stdin question", "auto")]
    assert fake.close_calls == 4


def test_chat_keyboard_interrupt_still_closes_client(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeCLIClient()
    monkeypatch.setattr(
        AssistantClient,
        "auto",
        classmethod(lambda cls: cast(AssistantClient, fake)),
    )

    def interrupt(prompt: str) -> str:
        del prompt
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)
    cli.cmd_chat(argparse.Namespace(model="auto"))

    assert "Bye" in capsys.readouterr().out
    assert fake.close_calls == 1


def test_run_tray_fallback_uses_one_service_and_update_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeService:
        url = "http://127.0.0.1:8420"

        def __init__(self, config: Any) -> None:
            del config

        def start(self) -> None:
            events.append("service.start")

        def stop(self) -> None:
            events.append("service.stop")

    class FakeTray:
        def __init__(self, config: Any, *, service: Any) -> None:
            del config, service

        def start(self) -> bool:
            events.append("tray.start")
            return False

    class ImmediateThread:
        def __init__(self, *, target: Callable[[], Any], daemon: bool) -> None:
            assert daemon
            self.target = target

        def start(self) -> None:
            self.target()

    config = SimpleNamespace(log_level="INFO", log_file=None)
    monkeypatch.setattr(config_module, "load_config", lambda: config)
    monkeypatch.setattr(logging_module, "setup_logging", Mock())
    monkeypatch.setattr(daemon_module, "KiroxService", FakeService)
    monkeypatch.setattr(tray_module, "KiroTray", FakeTray)
    monkeypatch.setattr(cli.threading, "Thread", ImmediateThread)
    update = Mock()
    monkeypatch.setattr(cli, "_check_update", update)
    wait = Mock()
    monkeypatch.setattr(cli, "_wait_for_interrupt", wait)

    cli.cmd_run(argparse.Namespace(no_update=False, no_tray=False, verbose=False))

    assert events == ["service.start", "tray.start", "service.stop"]
    update.assert_called_once_with()
    wait.assert_called_once()


def test_wait_for_interrupt_installs_restores_and_invokes_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    handlers: dict[signal.Signals, Any] = {}

    class FakeService:
        def wait(self) -> None:
            handlers[signal.SIGINT](signal.SIGINT, None)

        def stop(self) -> None:
            events.append("stop")

    monkeypatch.setattr(cli.signal, "getsignal", lambda signum: f"old-{signum}")

    def install(signum: signal.Signals, handler: Any) -> None:
        handlers[signum] = handler

    monkeypatch.setattr(cli.signal, "signal", install)
    cli._wait_for_interrupt(FakeService())

    assert events == ["stop"]
    assert handlers[signal.SIGINT] == f"old-{signal.SIGINT}"
    assert handlers[signal.SIGTERM] == f"old-{signal.SIGTERM}"


def test_main_default_dispatch_and_error_mapping(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run = Mock(return_value=None)
    monkeypatch.setattr(cli, "cmd_run", run)
    assert cli.main([]) is None
    namespace = run.call_args.args[0]
    assert namespace.command == "run"
    assert namespace.no_tray is False

    monkeypatch.setattr(cli, "cmd_status", Mock(side_effect=RuntimeError("failed")))
    assert cli.main(["status"]) == 1
    assert "Error: failed" in capsys.readouterr().err

    monkeypatch.setattr(cli, "cmd_status", Mock(side_effect=KeyboardInterrupt()))
    assert cli.main(["status"]) == 130

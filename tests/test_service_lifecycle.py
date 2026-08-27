"""Lifecycle tests without external credentials or network services."""

from __future__ import annotations

import argparse
import signal
import socket
import threading
import time
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

import pytest
from flask import Flask

import kirox.cli as cli
from kirox.core.auth import AuthManager
from kirox.core.client import AssistantClient
from kirox.service.daemon import KiroxService
from kirox.service.server import (
    CONTROL_SHUTDOWN_PATH,
    CONTROL_TOKEN_HEADER,
    ManagedHTTPServer,
    close_app_owned_client,
    create_app,
)
from kirox.service.state import read_state
from kirox.utils.config import Config, load_config


class FakeClient:
    def __init__(self) -> None:
        self.auth = SimpleNamespace(
            is_authenticated=True,
            profile_arn="profile",
            source="explicit",
        )
        self.close_calls = 0

    def list_models(self):
        return []

    def close(self) -> None:
        self.close_calls += 1


def _client() -> tuple[FakeClient, AssistantClient]:
    fake = FakeClient()
    return fake, cast(AssistantClient, fake)


def test_managed_service_owns_port_state_threads_and_client(tmp_path):
    fake, client = _client()
    state_path = tmp_path / "service.json"
    service = KiroxService(
        Config(server_port=0, auto_refresh=False),
        client=client,
        state_path=state_path,
    )

    service.start()
    service.start()
    state = read_state(state_path)
    assert state is not None
    assert state.process_identity is not None
    assert state.url == service.url
    port = int(state.url.rsplit(":", 1)[1])
    assert port > 0

    with socket.socket() as probe:
        with pytest.raises(OSError):
            probe.bind(("127.0.0.1", port))

    service.stop()
    service.stop()

    assert service.wait(0)
    assert read_state(state_path) is None
    assert fake.close_calls == 1
    assert service._server is not None and service._server.wait(1)
    assert not any(
        thread.name in {"kirox-http-server", "kirox-token-scheduler"} and thread.is_alive()
        for thread in threading.enumerate()
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))


def test_service_rolls_back_and_closes_client_once(tmp_path):
    fake, client = _client()
    service = KiroxService(
        Config(server_port=0, auto_refresh=False),
        client=client,
        state_path=tmp_path / "service.json",
    )

    with patch("kirox.service.daemon.write_state", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            service.start()

    service.stop()
    assert fake.close_calls == 1
    assert service.wait(0)
    assert service._server is not None and service._server.wait(1)


def test_control_endpoint_requires_loopback_and_constant_time_token():
    fake, client = _client()
    shutdown = Mock()
    app = create_app(
        Config(),
        client=client,
        shutdown_callback=shutdown,
        control_token="control-secret",
    )
    app.config["TESTING"] = True

    with app.test_client() as test_client:
        assert test_client.post(CONTROL_SHUTDOWN_PATH).status_code == 403
        assert (
            test_client.post(
                CONTROL_SHUTDOWN_PATH,
                headers={CONTROL_TOKEN_HEADER: "control-secret"},
                environ_overrides={"REMOTE_ADDR": "192.0.2.10"},
            ).status_code
            == 403
        )
        response = test_client.post(
            CONTROL_SHUTDOWN_PATH,
            headers={CONTROL_TOKEN_HEADER: "control-secret"},
        )

    assert response.status_code == 200
    shutdown.assert_called_once_with()
    assert fake.close_calls == 0


def test_server_rejects_non_loopback_bind():
    with pytest.raises(ValueError, match="loopback"):
        ManagedHTTPServer(Flask(__name__), host="0.0.0.0", port=0)


def test_cli_gracefully_stops_live_managed_service(monkeypatch, tmp_path):
    fake, client = _client()
    state_path = tmp_path / "service.json"
    monkeypatch.setenv("KIROX_STATE_FILE", str(state_path))
    service = KiroxService(
        Config(server_port=0, auto_refresh=False),
        client=client,
        state_path=state_path,
    )

    service.start()
    try:
        assert cli.cmd_stop(argparse.Namespace(force=False)) == 0
        assert service.wait(1)
        assert not service.is_running
        assert read_state(state_path) is None
        assert fake.close_calls == 1
    finally:
        service.stop()


def test_app_closes_only_the_client_it_created_itself(monkeypatch):
    fake, _ = _client()
    constructed: list[tuple[str, str]] = []

    def fake_assistant_client(*, auth, region):
        constructed.append((auth.source, region))
        return fake

    monkeypatch.setattr("kirox.service.server.AssistantClient", fake_assistant_client)
    for name in ("KIROX_TOKEN", "KIROX_PROFILE_ARN", "ASSISTANT_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    app = create_app(Config(token="config-token", profile_arn="arn:test", region="eu-west-1"))
    app.config["TESTING"] = True

    with app.test_client() as test_client:
        status = test_client.get("/api/token/status")

    assert status.status_code == 200
    assert status.get_json() == {
        "authenticated": True,
        "has_profile": True,
        "source": "explicit",
    }
    assert constructed == [("config", "eu-west-1")]

    close_app_owned_client(app)
    assert fake.close_calls == 1

    # A second teardown must not double-close the same client.
    close_app_owned_client(app)
    assert fake.close_calls == 1


def test_teardown_is_terminal_and_never_resurrects_a_client(monkeypatch):
    fake, _ = _client()
    built = 0

    def fake_assistant_client(*, auth, region):
        nonlocal built
        del auth, region
        built += 1
        return fake

    monkeypatch.setattr("kirox.service.server.AssistantClient", fake_assistant_client)
    monkeypatch.delenv("KIROX_TOKEN", raising=False)
    app = create_app(Config(token="config-token"))
    app.config["TESTING"] = True

    with app.test_client() as test_client:
        assert test_client.get("/api/token/status").status_code == 200
        close_app_owned_client(app)
        after = test_client.get("/api/token/status")

    assert built == 1
    assert fake.close_calls == 1
    assert after.status_code == 500
    assert after.get_json() == {"error": "Internal server error"}


def test_token_status_reports_environment_provenance(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "kirox.service.server.AssistantClient",
        lambda *, auth, region: SimpleNamespace(auth=auth, close=lambda: None),
    )
    monkeypatch.setenv("KIROX_TOKEN", "environment-token")
    monkeypatch.delenv("KIROX_PROFILE_ARN", raising=False)
    # load_config() overlays KIROX_TOKEN into config.token, so a naive label
    # would call an environment credential a config credential.
    app = create_app(load_config(tmp_path / "absent.json"))
    app.config["TESTING"] = True

    with app.test_client() as test_client:
        status = test_client.get("/api/token/status")

    assert status.get_json() == {
        "authenticated": True,
        "has_profile": False,
        "source": "environment:KIROX",
    }


def test_app_never_closes_an_injected_client():
    fake, client = _client()
    app = create_app(Config(), client=client)

    close_app_owned_client(app)

    assert fake.close_calls == 0


def test_teardown_survives_a_failing_client_close(caplog, monkeypatch):
    class ExplodingClient(FakeClient):
        def close(self) -> None:
            super().close()
            raise OSError("socket already gone")

    fake = ExplodingClient()
    monkeypatch.delenv("KIROX_TOKEN", raising=False)
    app = create_app(Config(token="config-token"))
    with patch("kirox.service.server.AssistantClient", lambda **kwargs: fake):
        app.config["TESTING"] = True
        with app.test_client() as test_client:
            assert test_client.get("/api/token/status").status_code == 200

    with caplog.at_level("WARNING"):
        close_app_owned_client(app)

    assert fake.close_calls == 1
    assert "Failed to close app-owned Kirox client" in caplog.text


def test_run_server_closes_the_client_the_app_created(monkeypatch):
    from kirox.service import server as server_module

    closed: list[bool] = []
    app = Flask(__name__)
    app.extensions[server_module.APP_CLIENT_CLOSER] = lambda: closed.append(True)
    stopped: list[bool] = []

    monkeypatch.setattr(server_module, "create_app", lambda config: app)
    monkeypatch.setattr(
        server_module,
        "ManagedHTTPServer",
        lambda application, *, host, port: SimpleNamespace(
            start=lambda: None,
            wait=lambda timeout=None: True,
            stop=lambda: stopped.append(True),
        ),
    )

    server_module.run_server(Config(server_port=0))

    assert stopped == [True]
    assert closed == [True]


def test_run_server_still_tears_down_after_a_keyboard_interrupt(monkeypatch):
    from kirox.service import server as server_module

    closed: list[bool] = []
    app = Flask(__name__)
    app.extensions[server_module.APP_CLIENT_CLOSER] = lambda: closed.append(True)

    def interrupt():
        raise KeyboardInterrupt

    monkeypatch.setattr(server_module, "create_app", lambda config: app)
    monkeypatch.setattr(
        server_module,
        "ManagedHTTPServer",
        lambda application, *, host, port: SimpleNamespace(
            start=lambda: None,
            wait=lambda timeout=None: interrupt(),
            stop=lambda: None,
        ),
    )

    server_module.run_server()

    assert closed == [True]


def test_properties_are_safe_before_start_and_after_stop(tmp_path):
    fake, client = _client()
    service = KiroxService(
        Config(server_port=0, auto_refresh=False),
        client=client,
        state_path=tmp_path / "service.json",
    )

    assert service.is_running is False
    assert service.url is None
    assert service.control_token is None

    service.start()
    try:
        assert service.is_running
        assert service.url is not None and service.url.startswith("http://127.0.0.1:")
        assert service.control_token
    finally:
        service.stop()

    assert service.is_running is False
    assert fake.close_calls == 1


def test_start_is_idempotent_and_refuses_restart_after_shutdown(tmp_path):
    fake, client = _client()
    service = KiroxService(
        Config(server_port=0, auto_refresh=False),
        client=client,
        state_path=tmp_path / "service.json",
    )

    service.start()
    first_url = service.url
    service.start()
    assert service.url == first_url

    service.stop()
    service.stop()
    assert fake.close_calls == 1

    with pytest.raises(RuntimeError, match="cannot be restarted"):
        service.start()


def test_failed_state_write_rolls_every_started_component_back(tmp_path, monkeypatch):
    fake, client = _client()
    service = KiroxService(
        Config(server_port=0, auto_refresh=False),
        client=client,
        state_path=tmp_path / "service.json",
    )
    monkeypatch.setattr(
        "kirox.service.daemon.write_state",
        Mock(side_effect=OSError("state volume is read-only")),
    )

    with pytest.raises(OSError, match="read-only"):
        service.start()

    assert service.is_running is False
    assert service._scheduler is not None and not service._scheduler.is_running
    assert service._server is not None and service._server.wait(1)
    assert fake.close_calls == 1
    assert not (tmp_path / "service.json").exists()
    assert service.wait(0) is True


def test_wait_times_out_while_running_and_notices_a_dead_server(tmp_path):
    fake, client = _client()
    service = KiroxService(
        Config(server_port=0, auto_refresh=False),
        client=client,
        state_path=tmp_path / "service.json",
    )
    service.start()
    try:
        assert service.wait(0.05) is False
        assert service.is_running

        # Simulate the serving thread exiting on its own.
        assert service._server is not None
        service._server.stop()

        assert service.wait(2) is True
        assert not service.is_running
    finally:
        service.stop()

    assert fake.close_calls == 1


def test_run_installs_signal_handlers_stops_cleanly_and_restores_them(tmp_path):
    fake, client = _client()
    service = KiroxService(
        Config(server_port=0, auto_refresh=False),
        client=client,
        state_path=tmp_path / "service.json",
    )
    watched = (signal.SIGINT, signal.SIGTERM)
    before = {signum: signal.getsignal(signum) for signum in watched}
    observed: dict[int, object] = {}

    def stopper() -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not service.is_running:
            time.sleep(0.01)
        for signum in watched:
            observed[signum] = signal.getsignal(signum)
        service.stop()

    thread = threading.Thread(target=stopper, name="test-stopper")
    thread.start()
    try:
        service.run()
    finally:
        thread.join(10)

    assert set(observed) == set(watched)
    for signum in watched:
        assert observed[signum] is not before[signum]
        assert signal.getsignal(signum) is before[signum]
    assert service.is_running is False
    assert fake.close_calls == 1
    assert read_state(tmp_path / "service.json") is None


def test_signal_handler_stops_the_running_service(tmp_path):
    fake, client = _client()
    service = KiroxService(
        Config(server_port=0, auto_refresh=False),
        client=client,
        state_path=tmp_path / "service.json",
    )
    watched = (signal.SIGINT, signal.SIGTERM)
    before = {signum: signal.getsignal(signum) for signum in watched}

    def raise_signal() -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not service.is_running:
            time.sleep(0.01)
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(int(signal.SIGTERM), None)

    thread = threading.Thread(target=raise_signal, name="test-signaller")
    thread.start()
    try:
        service.run()
    finally:
        thread.join(10)

    assert service.is_running is False
    assert fake.close_calls == 1
    for signum in watched:
        assert signal.getsignal(signum) is before[signum]


def test_service_callbacks_log_without_raising(caplog):
    fake, client = _client()
    service = KiroxService(Config(auto_refresh=False), client=client)

    with caplog.at_level("DEBUG"):
        service._on_token_refresh()
        service._on_error(RuntimeError("scheduler could not reach upstream"))

    assert "Token refreshed" in caplog.text
    assert "scheduler could not reach upstream" in caplog.text
    assert fake.close_calls == 0


def test_created_client_resolves_credentials_from_configuration(monkeypatch):
    resolved = AuthManager(token="config-token", source="config")
    seen: list[object] = []

    def resolve(*, config):
        seen.append(config)
        return resolved

    monkeypatch.setattr("kirox.service.daemon.AuthManager", SimpleNamespace(resolve=resolve))
    config = Config(region="eu-west-1", auto_refresh=False)
    service = KiroxService(config)

    created = service._create_client()

    assert seen == [config]
    assert created.auth is resolved
    assert created._region == "eu-west-1"
    created.close()


def test_daemon_main_loads_config_configures_logging_and_runs(monkeypatch, tmp_path):
    from kirox.service import daemon as daemon_module

    config = Config(log_level="DEBUG", log_file=str(tmp_path / "kirox.log"), auto_refresh=False)
    run_calls: list[Config] = []
    logging_calls: list[tuple[str, object]] = []

    class FakeService:
        def __init__(self, given: Config) -> None:
            self._config = given

        def run(self) -> None:
            run_calls.append(self._config)

    monkeypatch.setattr(daemon_module, "load_config", lambda: config)
    monkeypatch.setattr(daemon_module, "KiroxService", FakeService)
    monkeypatch.setattr(
        "kirox.utils.logging.setup_logging",
        lambda level, log_file: logging_calls.append((level, log_file)),
    )

    daemon_module.main()

    assert run_calls == [config]
    assert logging_calls == [("DEBUG", tmp_path / "kirox.log")]

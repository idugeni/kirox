"""Lifecycle tests without external credentials or network services."""

from __future__ import annotations

import argparse
import socket
import threading
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

import pytest
from flask import Flask

import kirox.cli as cli
from kirox.core.client import AssistantClient
from kirox.service.daemon import KiroxService
from kirox.service.server import (
    CONTROL_SHUTDOWN_PATH,
    CONTROL_TOKEN_HEADER,
    ManagedHTTPServer,
    create_app,
)
from kirox.service.state import read_state
from kirox.utils.config import Config


class FakeClient:
    def __init__(self) -> None:
        self.auth = SimpleNamespace(is_authenticated=True, _profile_arn="profile")
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

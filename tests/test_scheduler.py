"""Tests for interruptible token scheduling."""

from __future__ import annotations

import threading
import time
from unittest.mock import Mock

import pytest

from kirox.core.auth import AuthManager
from kirox.core.client import AssistantClient
from kirox.core.errors import APIError
from kirox.service.scheduler import TokenScheduler
from kirox.utils.config import Config


@pytest.mark.parametrize("status", [401, 403])
def test_scheduler_auth_api_error_refreshes_and_retries_once(status):
    replacement = AuthManager(token="replacement")
    resolver = Mock(return_value=replacement)
    client = Mock(spec=AssistantClient)
    client.list_models.side_effect = [
        APIError("authentication failed", status),
        [],
    ]
    scheduler = TokenScheduler(
        client,
        Config(auto_refresh=True),
        resolver=resolver,
    )

    scheduler._check_and_refresh()

    resolver.assert_called_once_with()
    client.replace_auth.assert_called_once_with(replacement)
    assert client.list_models.call_count == 2


@pytest.mark.parametrize("status", [401, 403])
def test_scheduler_auth_api_error_retry_error_propagates(status):
    replacement = AuthManager(token="replacement")
    resolver = Mock(return_value=replacement)
    first_error = APIError("initial authentication failed", status)
    retry_error = APIError("retry authentication failed", status)
    client = Mock(spec=AssistantClient)
    client.list_models.side_effect = [first_error, retry_error]
    scheduler = TokenScheduler(
        client,
        Config(auto_refresh=True),
        resolver=resolver,
    )

    with pytest.raises(APIError) as raised:
        scheduler._check_and_refresh()

    assert raised.value is retry_error
    assert client.list_models.call_count == 2
    resolver.assert_called_once_with()
    client.replace_auth.assert_called_once_with(replacement)


def test_scheduler_non_auth_api_error_propagates_without_replacing_auth():
    error = APIError("expired upstream token", 500)
    resolver = Mock()
    client = Mock(spec=AssistantClient)
    client.list_models.side_effect = error
    scheduler = TokenScheduler(
        client,
        Config(auto_refresh=True),
        resolver=resolver,
    )

    with pytest.raises(APIError) as raised:
        scheduler._check_and_refresh()

    assert raised.value is error
    client.list_models.assert_called_once_with()
    resolver.assert_not_called()
    client.replace_auth.assert_not_called()


def test_scheduler_start_stop_is_idempotent_and_interrupts_wait():
    client = Mock(spec=AssistantClient)
    scheduler = TokenScheduler(
        client,
        Config(auto_refresh=False, refresh_interval=3600),
    )

    scheduler.start()
    first_thread = scheduler._thread
    scheduler.start()
    assert scheduler._thread is first_thread
    assert scheduler.is_running

    started = time.monotonic()
    scheduler.stop()
    scheduler.stop()

    assert time.monotonic() - started < 1.0
    assert not scheduler.is_running
    assert first_thread is not None and not first_thread.is_alive()
    assert not any(
        thread.name == "kirox-token-scheduler" and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_scheduler_uses_injected_resolver_and_atomic_replace():
    refreshed = threading.Event()
    replacement = AuthManager(token="replacement")
    resolver = Mock(return_value=replacement)
    client = Mock(spec=AssistantClient)
    client.list_models.side_effect = RuntimeError("expired token")
    scheduler = TokenScheduler(
        client,
        Config(auto_refresh=True, refresh_interval=3600),
        on_token_refresh=refreshed.set,
        resolver=resolver,
    )

    scheduler.start()
    assert refreshed.wait(1)
    scheduler.stop()

    resolver.assert_called_once_with()
    client.replace_auth.assert_called_once_with(replacement)


def test_scheduler_error_backoff_is_interruptible():
    failed = threading.Event()
    client = Mock(spec=AssistantClient)
    client.list_models.side_effect = RuntimeError("temporary failure")
    scheduler = TokenScheduler(
        client,
        Config(auto_refresh=True, refresh_interval=3600),
        on_error=lambda error: failed.set(),
    )

    scheduler.start()
    assert failed.wait(1)
    started = time.monotonic()
    scheduler.stop()

    assert time.monotonic() - started < 1.0


def test_scheduler_successful_check_notifies_refresh():
    client = Mock(spec=AssistantClient)
    client.list_models.return_value = []
    refreshed = Mock()
    scheduler = TokenScheduler(
        client,
        Config(auto_refresh=True),
        on_token_refresh=refreshed,
    )

    scheduler._check_and_refresh()

    refreshed.assert_called_once_with()

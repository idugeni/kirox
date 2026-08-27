"""Tests for the shared loopback host classifier."""

from __future__ import annotations

from typing import cast

import pytest

from kirox.utils.net import is_loopback_host


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "LocalHost",
        "127.0.0.1",
        "127.1.2.3",
        "::1",
        "[::1]",
        "::1%lo0",
        "::ffff:127.0.0.1",
    ],
)
def test_loopback_hosts_are_accepted(host: str) -> None:
    assert is_loopback_host(host) is True


@pytest.mark.parametrize(
    "host",
    [
        "",
        " ",
        "0.0.0.0",
        "10.0.0.1",
        "192.168.1.10",
        "::",
        "2001:db8::1",
        "::ffff:10.0.0.1",
        "localhost.attacker.example",
        "127.0.0.1.attacker.example",
        "example.com",
        "127.0.0.1:8420",
    ],
)
def test_non_loopback_and_unresolvable_hosts_are_rejected(host: str) -> None:
    assert is_loopback_host(host) is False


def test_non_string_input_is_rejected_without_raising() -> None:
    assert is_loopback_host(cast(str, None)) is False
    assert is_loopback_host(cast(str, b"127.0.0.1")) is False

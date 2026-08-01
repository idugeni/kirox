"""Tests for atomic service lifecycle state."""

from __future__ import annotations

import json
from typing import Any

from kirox.service.process_identity import ProcessIdentity
from kirox.service.state import ServiceState, clear_state, read_state, write_state


def _state(pid: int, token: str) -> ServiceState:
    return ServiceState(
        pid=pid,
        url="http://127.0.0.1:8420",
        started_at=float(pid),
        control_token=token,
        process_identity=ProcessIdentity("linux-proc-start", ("boot-id", str(pid * 10))),
    )


def test_state_round_trip_and_owner_safe_cleanup(tmp_path):
    state_path = tmp_path / "nested" / "service.json"
    first = _state(100, "first-token")
    replacement = _state(101, "replacement-token")

    write_state(first, state_path)
    assert read_state(state_path) == first
    assert not list(state_path.parent.glob("*.tmp"))

    write_state(replacement, state_path)
    assert clear_state(first, state_path) is False
    assert read_state(state_path) == replacement
    assert clear_state(replacement, state_path) is True
    assert read_state(state_path) is None


def test_state_reads_legacy_without_process_identity(tmp_path):
    state_path = tmp_path / "service.json"
    state_path.write_text(
        json.dumps(
            {
                "pid": 100,
                "url": "http://127.0.0.1:8420",
                "started_at": 1.0,
                "control_token": "legacy-token",
            }
        ),
        encoding="utf-8",
    )

    state = read_state(state_path)
    assert state is not None
    assert state.process_identity is None
    assert state.control_token == "legacy-token"


def test_state_rejects_missing_or_malformed_process_identity(tmp_path):
    state_path = tmp_path / "service.json"
    base: dict[str, Any] = {
        "pid": 100,
        "url": "http://127.0.0.1:8420",
        "started_at": 1.0,
        "control_token": "token",
        "schema_version": 2,
    }

    state_path.write_text(json.dumps(base), encoding="utf-8")
    assert read_state(state_path) is None

    base["process_identity"] = {"kind": "unknown", "value": ["1"]}
    state_path.write_text(json.dumps(base), encoding="utf-8")
    assert read_state(state_path) is None


def test_state_rejects_unvalidated_pid_and_non_loopback_url(tmp_path):
    state_path = tmp_path / "service.json"
    state_path.write_text(
        json.dumps(
            {
                "pid": True,
                "url": "http://example.com:8420",
                "started_at": 1.0,
                "control_token": "token",
            }
        ),
        encoding="utf-8",
    )

    assert read_state(state_path) is None

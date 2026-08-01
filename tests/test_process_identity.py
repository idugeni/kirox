"""Tests for PID-reuse-safe process identity handling."""

from __future__ import annotations

import signal
import subprocess
import sys
from typing import Any
from unittest.mock import Mock

import pytest

import kirox.service.process_identity as process_identity_module
from kirox.service.process_identity import (
    ProcessIdentity,
    ProcessIdentityMismatch,
    ProcessIdentityUnavailable,
)


def test_linux_force_stop_signals_open_pidfd_after_identity_match(monkeypatch) -> None:
    expected = ProcessIdentity("linux-proc-start", ("boot-id", "12345"))
    pidfd_open = Mock(return_value=77)
    send_signal = Mock()
    close = Mock()
    monkeypatch.setattr(process_identity_module.sys, "platform", "linux")
    monkeypatch.setattr(process_identity_module.os, "pidfd_open", pidfd_open, raising=False)
    monkeypatch.setattr(
        process_identity_module.signal,
        "pidfd_send_signal",
        send_signal,
        raising=False,
    )
    monkeypatch.setattr(process_identity_module.os, "close", close)
    monkeypatch.setattr(
        process_identity_module,
        "capture_process_identity",
        lambda pid: expected,
    )

    process_identity_module.terminate_process(4321, expected)

    pidfd_open.assert_called_once_with(4321, 0)
    send_signal.assert_called_once_with(77, getattr(signal, "SIGKILL", signal.SIGTERM))
    close.assert_called_once_with(77)


def test_linux_force_stop_refuses_reused_pid_before_signal(monkeypatch) -> None:
    expected = ProcessIdentity("linux-proc-start", ("boot-id", "12345"))
    replacement = ProcessIdentity("linux-proc-start", ("boot-id", "99999"))
    send_signal = Mock()
    close = Mock()
    monkeypatch.setattr(process_identity_module.sys, "platform", "linux")
    monkeypatch.setattr(
        process_identity_module.os,
        "pidfd_open",
        Mock(return_value=77),
        raising=False,
    )
    monkeypatch.setattr(
        process_identity_module.signal,
        "pidfd_send_signal",
        send_signal,
        raising=False,
    )
    monkeypatch.setattr(process_identity_module.os, "close", close)
    monkeypatch.setattr(
        process_identity_module,
        "capture_process_identity",
        lambda pid: replacement,
    )

    with pytest.raises(ProcessIdentityMismatch, match="different process"):
        process_identity_module.terminate_process(4321, expected)

    send_signal.assert_not_called()
    close.assert_called_once_with(77)


def test_force_stop_fails_closed_without_stable_platform_target(monkeypatch) -> None:
    identity = ProcessIdentity("darwin-proc-start", ("123", "456"))
    monkeypatch.setattr(process_identity_module.sys, "platform", "darwin")

    with pytest.raises(ProcessIdentityUnavailable, match="stable process handle"):
        process_identity_module.terminate_process(4321, identity)


@pytest.mark.parametrize(
    "kind,value",
    [
        ("unknown-kind", ("1",)),
        ("windows-creation-time", ("1", "2")),
        ("linux-proc-start", ("boot-id",)),
        ("darwin-proc-start", ("123",)),
        ("windows-creation-time", ("",)),
        ("windows-creation-time", (123,)),
        ("windows-creation-time", ("not-a-number",)),
        ("linux-proc-start", ("boot-id", "12.5")),
        ("darwin-proc-start", ("123", "-1")),
    ],
)
def test_process_identity_rejects_unusable_shapes(kind: str, value: tuple) -> None:
    with pytest.raises(ValueError):
        ProcessIdentity(kind, value)


def test_process_identity_rejects_non_tuple_value() -> None:
    list_value: Any = ["1"]
    with pytest.raises(ValueError):
        ProcessIdentity("windows-creation-time", list_value)


def test_process_identity_round_trips_through_serialized_state() -> None:
    identity = ProcessIdentity("linux-proc-start", ("boot-id", "12345"))

    serialized = identity.to_dict()

    assert serialized == {"kind": "linux-proc-start", "value": ["boot-id", "12345"]}
    assert ProcessIdentity.from_dict(serialized) == identity


@pytest.mark.parametrize(
    "data",
    [
        {"kind": "windows-creation-time"},
        {"kind": "windows-creation-time", "value": ["1"], "extra": 1},
        {"kind": "windows-creation-time", "value": ("1",)},
        {"kind": "windows-creation-time", "value": "1"},
        {"kind": "nope", "value": ["1"]},
    ],
)
def test_process_identity_from_dict_rejects_unexpected_payloads(data: dict) -> None:
    with pytest.raises(ValueError):
        ProcessIdentity.from_dict(data)


@pytest.mark.parametrize("pid", [0, -1, True, 1.0, "1"])
def test_capture_and_terminate_reject_invalid_pids(pid: Any) -> None:
    identity = ProcessIdentity("windows-creation-time", ("1",))
    with pytest.raises(ValueError):
        process_identity_module.capture_process_identity(pid)
    with pytest.raises(ValueError):
        process_identity_module.terminate_process(pid, identity)


def test_terminate_process_requires_validated_identity() -> None:
    dict_identity: Any = {"kind": "windows-creation-time"}
    with pytest.raises(ValueError, match="validated process identity"):
        process_identity_module.terminate_process(1234, dict_identity)


def test_capture_and_terminate_fail_closed_on_unsupported_platform(monkeypatch) -> None:
    monkeypatch.setattr(process_identity_module.sys, "platform", "sunos5")

    with pytest.raises(ProcessIdentityUnavailable, match="unsupported"):
        process_identity_module.capture_process_identity(1234)
    with pytest.raises(ProcessIdentityUnavailable, match="unsupported"):
        process_identity_module.terminate_process(
            1234,
            ProcessIdentity("windows-creation-time", ("1",)),
        )


def test_linux_force_stop_requires_pidfd_support(monkeypatch) -> None:
    monkeypatch.setattr(process_identity_module.sys, "platform", "linux")
    monkeypatch.delattr(process_identity_module.os, "pidfd_open", raising=False)

    with pytest.raises(ProcessIdentityUnavailable, match="pidfd"):
        process_identity_module.terminate_process(
            1234,
            ProcessIdentity("linux-proc-start", ("boot-id", "1")),
        )


class _FakeProcPath:
    """Minimal ``Path`` stand-in that serves canned ``/proc`` reads."""

    def __init__(self, path: str, contents: dict[str, object]) -> None:
        self._path = path
        self._contents = contents

    def read_text(self, encoding: str = "utf-8") -> str:
        value = self._contents.get(self._path)
        if isinstance(value, BaseException):
            raise value
        if value is None:
            raise FileNotFoundError(self._path)
        return str(value)


def _install_fake_proc(monkeypatch, contents: dict[str, object]) -> None:
    monkeypatch.setattr(process_identity_module.sys, "platform", "linux")
    monkeypatch.setattr(
        process_identity_module,
        "Path",
        lambda path: _FakeProcPath(str(path), contents),
    )


_LINUX_STAT = (
    "4321 (kirox service) "
    + " ".join(["S", *(str(index) for index in range(4, 22)), "8899"])
    + " 990 991"
)


def test_linux_identity_reads_boot_id_and_process_start_ticks(monkeypatch) -> None:
    _install_fake_proc(
        monkeypatch,
        {
            "/proc/4321/stat": _LINUX_STAT,
            "/proc/sys/kernel/random/boot_id": "boot-abc\n",
        },
    )

    identity = process_identity_module.capture_process_identity(4321)

    assert identity == ProcessIdentity("linux-proc-start", ("boot-abc", "8899"))


def test_linux_identity_reports_missing_process(monkeypatch) -> None:
    _install_fake_proc(monkeypatch, {"/proc/sys/kernel/random/boot_id": "boot-abc"})

    with pytest.raises(ProcessLookupError):
        process_identity_module.capture_process_identity(4321)


@pytest.mark.parametrize(
    "contents,message",
    [
        (
            {"/proc/4321/stat": PermissionError(13, "denied")},
            "cannot read Linux process identity",
        ),
        ({"/proc/4321/stat": "4321 (kirox) S 1 2 3"}, "malformed"),
        (
            {
                "/proc/4321/stat": _LINUX_STAT,
                "/proc/sys/kernel/random/boot_id": OSError(5, "io"),
            },
            "cannot read Linux boot identity",
        ),
        (
            {"/proc/4321/stat": _LINUX_STAT, "/proc/sys/kernel/random/boot_id": "  \n"},
            "boot identity is empty",
        ),
    ],
)
def test_linux_identity_fails_closed_on_unusable_proc_data(
    monkeypatch, contents: dict, message: str
) -> None:
    _install_fake_proc(monkeypatch, contents)

    with pytest.raises(ProcessIdentityUnavailable, match=message):
        process_identity_module.capture_process_identity(4321)


def test_windows_process_api_absence_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(process_identity_module.sys, "platform", "win32")
    monkeypatch.delattr(process_identity_module.ctypes, "WinDLL", raising=False)

    with pytest.raises(ProcessIdentityUnavailable, match="WinDLL"):
        process_identity_module.capture_process_identity(4321)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows error mapping requires Windows")
@pytest.mark.parametrize(
    "code,expected",
    [(87, ProcessLookupError), (1168, ProcessLookupError), (5, PermissionError), (1450, OSError)],
)
def test_windows_error_codes_map_to_process_semantics(code: int, expected: type) -> None:
    error = process_identity_module._windows_error(code)

    assert type(error) is expected
    assert error.args[0] == code


@pytest.mark.skipif(
    sys.platform not in {"linux", "win32"},
    reason="identity-bound termination is only implemented for Linux and Windows",
)
def test_identity_bound_force_stop_spares_reused_pid_and_kills_real_owner() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        identity = process_identity_module.capture_process_identity(child.pid)
        assert process_identity_module.capture_process_identity(child.pid) == identity

        stale = ProcessIdentity(identity.kind, (*identity.value[:-1], "1"))
        with pytest.raises(ProcessIdentityMismatch):
            process_identity_module.terminate_process(child.pid, stale)
        assert child.poll() is None

        process_identity_module.terminate_process(child.pid, identity)
        assert child.wait(timeout=30) is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=30)

"""Cross-platform process identity and identity-bound termination."""

from __future__ import annotations

import ctypes
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class ProcessIdentityUnavailable(RuntimeError):
    """Raised when the operating system cannot provide a safe process identity."""


class ProcessIdentityMismatch(RuntimeError):
    """Raised when a PID no longer refers to the persisted process identity."""


_IDENTITY_PARTS = {
    "linux-proc-start": 2,
    "windows-creation-time": 1,
    "darwin-proc-start": 2,
}


@dataclass(frozen=True)
class ProcessIdentity:
    """Kernel-derived identity that distinguishes reuse of the same PID."""

    kind: str
    value: tuple[str, ...]

    def __post_init__(self) -> None:
        expected_parts = _IDENTITY_PARTS.get(self.kind)
        if expected_parts is None:
            raise ValueError("unsupported process identity kind")
        if type(self.value) is not tuple or len(self.value) != expected_parts:
            raise ValueError("process identity has an invalid value")
        if any(not isinstance(part, str) or not part for part in self.value):
            raise ValueError("process identity parts must be non-empty strings")
        numeric_parts = self.value[1:] if self.kind == "linux-proc-start" else self.value
        if any(not part.isascii() or not part.isdecimal() for part in numeric_parts):
            raise ValueError("process identity time parts must be decimal integers")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ProcessIdentity:
        if frozenset(data) != frozenset({"kind", "value"}):
            raise ValueError("process identity has unexpected fields")
        value = data["value"]
        if not isinstance(value, list):
            raise ValueError("process identity value must be a list")
        return cls(kind=data["kind"], value=tuple(value))

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "value": list(self.value)}


def _linux_process_identity(pid: int) -> ProcessIdentity:
    try:
        stat_data = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ProcessLookupError(pid) from error
    except OSError as error:
        raise ProcessIdentityUnavailable("cannot read Linux process identity") from error

    closing_parenthesis = stat_data.rfind(")")
    fields = stat_data[closing_parenthesis + 2 :].split() if closing_parenthesis >= 0 else []
    if len(fields) <= 19 or not fields[19].isascii() or not fields[19].isdecimal():
        raise ProcessIdentityUnavailable("Linux process identity is malformed")
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError as error:
        raise ProcessIdentityUnavailable("cannot read Linux boot identity") from error
    if not boot_id:
        raise ProcessIdentityUnavailable("Linux boot identity is empty")
    return ProcessIdentity("linux-proc-start", (boot_id, fields[19]))


def _ctypes_api(name: str) -> Any:
    try:
        return vars(ctypes)[name]
    except KeyError as error:
        raise ProcessIdentityUnavailable(f"Windows process API {name} is unavailable") from error


def _windows_error(code: int) -> OSError:
    format_error = _ctypes_api("FormatError")
    message = format_error(code)
    if code in {87, 1168}:
        return ProcessLookupError(code, message)
    if code == 5:
        return PermissionError(code, message)
    return OSError(code, message)


def _windows_process_identity(pid: int, access: int = 0x1000) -> ProcessIdentity:
    class FileTime(ctypes.Structure):
        _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    win_dll = _ctypes_api("WinDLL")
    kernel32 = win_dll("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
    ]
    kernel32.GetProcessTimes.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    handle = kernel32.OpenProcess(access, False, pid)
    if not handle:
        get_last_error = _ctypes_api("get_last_error")
        raise _windows_error(get_last_error())
    try:
        creation = FileTime()
        exit_time = FileTime()
        kernel_time = FileTime()
        user_time = FileTime()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            get_last_error = _ctypes_api("get_last_error")
            raise _windows_error(get_last_error())
        creation_time = (creation.high << 32) | creation.low
        return ProcessIdentity("windows-creation-time", (str(creation_time),))
    finally:
        kernel32.CloseHandle(handle)


def _darwin_process_identity(pid: int) -> ProcessIdentity:
    class ProcBSDInfo(ctypes.Structure):
        _fields_ = [
            ("flags", ctypes.c_uint32),
            ("status", ctypes.c_uint32),
            ("xstatus", ctypes.c_uint32),
            ("pid", ctypes.c_uint32),
            ("ppid", ctypes.c_uint32),
            ("uid", ctypes.c_uint32),
            ("gid", ctypes.c_uint32),
            ("ruid", ctypes.c_uint32),
            ("rgid", ctypes.c_uint32),
            ("svuid", ctypes.c_uint32),
            ("svgid", ctypes.c_uint32),
            ("rfu_1", ctypes.c_uint32),
            ("comm", ctypes.c_char * 17),
            ("name", ctypes.c_char * 32),
            ("nfiles", ctypes.c_uint32),
            ("pgid", ctypes.c_uint32),
            ("pjobc", ctypes.c_uint32),
            ("e_tdev", ctypes.c_uint32),
            ("e_tpgid", ctypes.c_uint32),
            ("nice", ctypes.c_int32),
            ("start_tvsec", ctypes.c_uint64),
            ("start_tvusec", ctypes.c_uint64),
        ]

    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    library.proc_pidinfo.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    library.proc_pidinfo.restype = ctypes.c_int
    info = ProcBSDInfo()
    size = library.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
    if size <= 0:
        error_code = ctypes.get_errno()
        if error_code == 3:
            raise ProcessLookupError(error_code, os.strerror(error_code))
        if error_code in {1, 13}:
            raise PermissionError(error_code, os.strerror(error_code))
        raise ProcessIdentityUnavailable("cannot read macOS process identity")
    if size != ctypes.sizeof(info) or info.pid != pid:
        raise ProcessIdentityUnavailable("macOS process identity is malformed")
    return ProcessIdentity(
        "darwin-proc-start",
        (str(info.start_tvsec), str(info.start_tvusec)),
    )


def capture_process_identity(pid: int) -> ProcessIdentity:
    """Capture the kernel-derived identity for ``pid`` or fail closed."""
    if type(pid) is not int or pid <= 0:
        raise ValueError("process PID must be a positive integer")
    if sys.platform.startswith("linux"):
        return _linux_process_identity(pid)
    if sys.platform == "win32":
        return _windows_process_identity(pid)
    if sys.platform == "darwin":
        return _darwin_process_identity(pid)
    raise ProcessIdentityUnavailable(f"process identity is unsupported on {sys.platform}")


def _terminate_linux(pid: int, expected: ProcessIdentity) -> None:
    pidfd_open = getattr(os, "pidfd_open", None)
    pidfd_send_signal = getattr(signal, "pidfd_send_signal", None)
    if pidfd_open is None or pidfd_send_signal is None:
        raise ProcessIdentityUnavailable("safe force-stop requires Linux pidfd support")

    pidfd = pidfd_open(pid, 0)
    try:
        current = capture_process_identity(pid)
        if current != expected:
            raise ProcessIdentityMismatch("PID belongs to a different process")
        pidfd_send_signal(pidfd, getattr(signal, "SIGKILL", signal.SIGTERM))
    finally:
        os.close(pidfd)


def _terminate_windows(pid: int, expected: ProcessIdentity) -> None:
    process_terminate = 0x0001
    process_query_limited_information = 0x1000

    class FileTime(ctypes.Structure):
        _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    win_dll = _ctypes_api("WinDLL")
    kernel32 = win_dll("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
    ]
    kernel32.GetProcessTimes.restype = ctypes.c_int
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.TerminateProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    handle = kernel32.OpenProcess(
        process_terminate | process_query_limited_information,
        False,
        pid,
    )
    if not handle:
        get_last_error = _ctypes_api("get_last_error")
        raise _windows_error(get_last_error())
    try:
        creation = FileTime()
        exit_time = FileTime()
        kernel_time = FileTime()
        user_time = FileTime()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            get_last_error = _ctypes_api("get_last_error")
            raise _windows_error(get_last_error())
        current = ProcessIdentity(
            "windows-creation-time",
            (str((creation.high << 32) | creation.low),),
        )
        if current != expected:
            raise ProcessIdentityMismatch("PID belongs to a different process")
        if not kernel32.TerminateProcess(handle, 1):
            get_last_error = _ctypes_api("get_last_error")
            raise _windows_error(get_last_error())
    finally:
        kernel32.CloseHandle(handle)


def terminate_process(pid: int, expected: ProcessIdentity) -> None:
    """Terminate only the stable process target whose identity matches ``expected``."""
    if type(pid) is not int or pid <= 0:
        raise ValueError("process PID must be a positive integer")
    if not isinstance(expected, ProcessIdentity):
        raise ValueError("a validated process identity is required")
    if sys.platform.startswith("linux"):
        _terminate_linux(pid, expected)
        return
    if sys.platform == "win32":
        _terminate_windows(pid, expected)
        return
    if sys.platform == "darwin":
        raise ProcessIdentityUnavailable(
            "safe force-stop is unavailable on macOS without a stable process handle"
        )
    raise ProcessIdentityUnavailable(f"safe force-stop is unsupported on {sys.platform}")

"""Atomic lifecycle state for the local Kirox service."""

from __future__ import annotations

import ipaddress
import json
import math
import os
import stat
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

from kirox.service.process_identity import ProcessIdentity

DEFAULT_STATE_PATH = Path.home() / ".kirox" / "service.json"
_LEGACY_STATE_KEYS = frozenset({"pid", "url", "started_at", "control_token"})
_STATE_KEYS = _LEGACY_STATE_KEYS | {"schema_version", "process_identity"}
_STATE_SCHEMA_VERSION = 2
_STATE_LOCK = threading.RLock()


def _is_loopback(host: str) -> bool:
    normalized = host.strip("[]").split("%", 1)[0].lower()
    if normalized == "localhost":
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


def _validate_url(url: object) -> str:
    if not isinstance(url, str) or not url:
        raise ValueError("service URL must be a non-empty string")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise ValueError("service URL is invalid") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname is None
        or not _is_loopback(parsed.hostname)
        or port is None
        or port <= 0
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("service URL must target a loopback HTTP port")
    return url.rstrip("/")


def get_state_path(path: Optional[Path] = None) -> Path:
    """Return an explicit path, an environment override, or the user default."""
    if path is not None:
        return Path(path)
    override = os.environ.get("KIROX_STATE_FILE")
    return Path(override).expanduser() if override else DEFAULT_STATE_PATH


@dataclass(frozen=True)
class ServiceState:
    """Validated identity and control data for one service process."""

    pid: int
    url: str
    started_at: float
    control_token: str = field(repr=False)
    process_identity: Optional[ProcessIdentity] = None

    def __post_init__(self) -> None:
        if type(self.pid) is not int or self.pid <= 0:
            raise ValueError("service PID must be a positive integer")
        object.__setattr__(self, "url", _validate_url(self.url))
        if (
            isinstance(self.started_at, bool)
            or not isinstance(self.started_at, (int, float))
            or not math.isfinite(self.started_at)
            or self.started_at <= 0
        ):
            raise ValueError("service start time must be a positive finite number")
        if not isinstance(self.control_token, str) or not self.control_token:
            raise ValueError("service control token must be a non-empty string")
        if self.process_identity is not None and not isinstance(
            self.process_identity, ProcessIdentity
        ):
            raise ValueError("service process identity is invalid")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ServiceState:
        keys = frozenset(data)
        if keys == _LEGACY_STATE_KEYS:
            process_identity = None
        elif keys == _STATE_KEYS:
            if data["schema_version"] != _STATE_SCHEMA_VERSION:
                raise ValueError("unsupported service state schema")
            identity_data = data["process_identity"]
            if not isinstance(identity_data, Mapping):
                raise ValueError("service process identity is invalid")
            process_identity = ProcessIdentity.from_dict(identity_data)
        else:
            raise ValueError("service state has unexpected fields")
        return cls(
            pid=data["pid"],
            url=data["url"],
            started_at=data["started_at"],
            control_token=data["control_token"],
            process_identity=process_identity,
        )

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "pid": self.pid,
            "url": self.url,
            "started_at": self.started_at,
            "control_token": self.control_token,
        }
        if self.process_identity is not None:
            data["schema_version"] = _STATE_SCHEMA_VERSION
            data["process_identity"] = self.process_identity.to_dict()
        return data


def _read_state_unlocked(path: Path) -> Optional[ServiceState]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return ServiceState.from_dict(data)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None


def read_state(path: Optional[Path] = None) -> Optional[ServiceState]:
    """Read state only when every persisted field validates."""
    state_path = get_state_path(path)
    with _STATE_LOCK:
        return _read_state_unlocked(state_path)


def write_state(state: ServiceState, path: Optional[Path] = None) -> None:
    """Atomically replace the state file with owner-only permissions where supported."""
    state_path = get_state_path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Optional[Path] = None
    with _STATE_LOCK:
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=state_path.parent,
                prefix=f".{state_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(state.to_dict(), temporary, separators=(",", ":"))
                temporary.flush()
                os.fsync(temporary.fileno())
            try:
                os.chmod(temporary_path, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            os.replace(temporary_path, state_path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass


def clear_state(owner: ServiceState, path: Optional[Path] = None) -> bool:
    """Remove state only if it still belongs to the exact service owner."""
    state_path = get_state_path(path)
    with _STATE_LOCK:
        if _read_state_unlocked(state_path) != owner:
            return False
        try:
            state_path.unlink()
        except FileNotFoundError:
            return False
        return True

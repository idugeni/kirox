"""Configuration management."""

from __future__ import annotations

import json
import logging
import os
import stat
import tempfile
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, Optional

from kirox.utils.net import is_loopback_host

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_NAME = "config.json"
_LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET")
# `setup_logging()` resolved levels with getattr(logging, name), so these
# aliases loaded in earlier versions. Normalize instead of rejecting them.
_LOG_LEVEL_ALIASES = {"WARN": "WARNING", "FATAL": "CRITICAL"}


class ConfigError(ValueError):
    """A field-addressable configuration contract violation."""

    def __init__(self, field_name: str, message: str) -> None:
        super().__init__(f"config field {field_name!r} {message}")
        self.field_name = field_name


def _require_str(field_name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(field_name, "must be a non-empty string")
    return value.strip()


def _require_int(field_name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(field_name, "must be an integer")
    return value


def _require_optional_str(field_name: str, value: object) -> Optional[str]:
    if value is None:
        return None
    return _require_str(field_name, value)


@dataclass
class Config:
    region: str = "us-east-1"
    server_port: int = 8420
    server_host: str = "127.0.0.1"
    auto_refresh: bool = True
    refresh_interval: int = 3000
    log_level: str = "INFO"
    log_file: Optional[str] = None
    token: Optional[str] = None
    profile_arn: Optional[str] = None
    db_path: Optional[str] = None

    def __post_init__(self) -> None:
        """Reject invalid values here so startup fails with a named field."""
        self.region = _require_str("region", self.region)

        self.server_port = _require_int("server_port", self.server_port)
        if not 0 <= self.server_port <= 65535:
            raise ConfigError("server_port", "must be between 0 and 65535")

        self.server_host = _require_str("server_host", self.server_host)
        if not is_loopback_host(self.server_host):
            raise ConfigError("server_host", "must be a loopback host; Kirox never binds publicly")

        if not isinstance(self.auto_refresh, bool):
            raise ConfigError("auto_refresh", "must be a boolean")

        self.refresh_interval = _require_int("refresh_interval", self.refresh_interval)
        if self.refresh_interval <= 0:
            raise ConfigError("refresh_interval", "must be a positive number of seconds")

        self.log_level = _require_str("log_level", self.log_level).upper()
        self.log_level = _LOG_LEVEL_ALIASES.get(self.log_level, self.log_level)
        if self.log_level not in _LOG_LEVELS:
            raise ConfigError("log_level", f"must be one of {', '.join(_LOG_LEVELS)}")

        self.log_file = _require_optional_str("log_file", self.log_file)
        self.token = _require_optional_str("token", self.token)
        self.profile_arn = _require_optional_str("profile_arn", self.profile_arn)
        self.db_path = _require_optional_str("db_path", self.db_path)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        """Build a validated config, ignoring keys this version does not know."""
        if not isinstance(data, dict):
            raise ConfigError("body", "must be a JSON object")
        known = {field.name for field in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})

    @classmethod
    def from_file(cls, path: Path) -> Config:
        """Load JSON UTF-8 configuration, falling back to defaults when absent."""
        if not path.exists():
            return cls()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ConfigError("body", "must be UTF-8 encoded") from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError("body", f"must be valid JSON ({exc.msg})") from exc
        return cls.from_dict(data)

    def to_file(self, path: Path) -> None:
        """Write the config atomically with owner-only permissions.

        The file can hold a bearer token, so it is created private and replaced
        in one step instead of being truncated in place. Replacing the file
        resets its permissions, so permissions set by hand on a previous file do
        not survive a write.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        self._remove_stale_temporary_files(path)
        payload = json.dumps(
            {field.name: getattr(self, field.name) for field in fields(self)},
            indent=2,
            ensure_ascii=False,
        )
        temporary_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            try:
                os.chmod(temporary_path, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            os.replace(temporary_path, path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass

    @staticmethod
    def _remove_stale_temporary_files(path: Path) -> None:
        """Delete temp siblings a killed process left behind.

        A crash between the write and the replace leaves a private temporary
        file that may contain a token, and nothing else would ever remove it.
        """
        for stale in path.parent.glob(f".{path.name}.*.tmp"):
            try:
                stale.unlink()
            except OSError:
                logger.debug("Could not remove stale config temporary file", exc_info=True)


def default_config_path() -> Path:
    """Resolve the per-user default path when asked, not at import time."""
    return Path.home() / ".kirox" / DEFAULT_CONFIG_NAME


def get_config_path(config_path: Optional[Path] = None) -> Path:
    """Return an explicit path, a `KIROX_CONFIG` override, or the user default."""
    if config_path is not None:
        return Path(config_path)
    override = os.environ.get("KIROX_CONFIG")
    return Path(override).expanduser() if override else default_config_path()


def load_config(config_path: Optional[Path] = None) -> Config:
    """Load the config file, then overlay `KIROX_*` environment values."""
    config = Config.from_file(get_config_path(config_path))
    overlays: dict[str, Any] = {}
    for variable, field_name in (
        ("KIROX_TOKEN", "token"),
        ("KIROX_PROFILE_ARN", "profile_arn"),
        ("KIROX_REGION", "region"),
        ("KIROX_SERVER_HOST", "server_host"),
        ("KIROX_LOG_LEVEL", "log_level"),
        ("KIROX_LOG_FILE", "log_file"),
    ):
        value = os.environ.get(variable)
        if value:
            overlays[field_name] = value
    raw_port = os.environ.get("KIROX_SERVER_PORT", "").strip()
    if raw_port:
        if not raw_port.isascii() or not raw_port.isdigit():
            raise ConfigError("server_port", "must be an integer")
        overlays["server_port"] = int(raw_port)
    if not overlays:
        return config
    # replace() re-runs __post_init__, so overlaid values are validated too.
    return replace(config, **overlays)

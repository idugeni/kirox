"""Deterministic authentication credential resolution."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Optional

from kirox.core.errors import AuthenticationError

_ENV_PREFIXES = ("KIROX", "ASSISTANT")


def _nonempty_string(value: object | None) -> Optional[str]:
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _config_value(config: object | None, key: str) -> object | None:
    if config is None:
        return None
    if isinstance(config, Mapping):
        return config.get(key)
    return getattr(config, key, None)


class AuthManager:
    """Resolve and manage bearer credentials without secret discovery scans."""

    def __init__(
        self,
        token: Optional[str] = None,
        profile_arn: Optional[str] = None,
        *,
        source: str = "unknown",
    ):
        self._token = token
        self._profile_arn = profile_arn
        self._source = source

    @classmethod
    def resolve(
        cls,
        *,
        token: Optional[str] = None,
        profile_arn: Optional[str] = None,
        db_path: Optional[str | Path] = None,
        config: object | None = None,
        environ: Optional[Mapping[str, str]] = None,
    ) -> AuthManager:
        """Resolve explicit/config, KIROX, ASSISTANT, then fixed CLI DB sources."""
        return cls._resolve(
            token=token,
            profile_arn=profile_arn,
            db_path=db_path,
            config=config,
            environ=os.environ if environ is None else environ,
            include_direct=True,
            include_environment=True,
            include_cli_db=True,
        )

    @classmethod
    def auto_detect(
        cls,
        *,
        token: Optional[str] = None,
        profile_arn: Optional[str] = None,
        db_path: Optional[str | Path] = None,
        config: object | None = None,
    ) -> AuthManager:
        """Backward-compatible delegate to the deterministic resolver."""
        return cls.resolve(
            token=token,
            profile_arn=profile_arn,
            db_path=db_path,
            config=config,
        )

    @classmethod
    def from_env(cls) -> AuthManager:
        """Load KIROX variables before their ASSISTANT aliases."""
        return cls._resolve(
            environ=os.environ,
            include_direct=False,
            include_environment=True,
            include_cli_db=False,
        )

    @classmethod
    def from_cli_db(cls, db_path: Optional[str | Path] = None) -> AuthManager:
        """Load an explicit or fixed-location kiro-cli database."""
        return cls._resolve(
            db_path=db_path,
            environ={},
            include_direct=False,
            include_environment=False,
            include_cli_db=True,
        )

    @classmethod
    def _resolve(
        cls,
        *,
        token: Optional[str] = None,
        profile_arn: Optional[str] = None,
        db_path: Optional[str | Path] = None,
        config: object | None = None,
        environ: Mapping[str, str],
        include_direct: bool,
        include_environment: bool,
        include_cli_db: bool,
    ) -> AuthManager:
        explicit_token = _nonempty_string(token) if include_direct else None
        config_token = _nonempty_string(_config_value(config, "token")) if include_direct else None
        explicit_profile = _nonempty_string(profile_arn) if include_direct else None
        config_profile = (
            _nonempty_string(_config_value(config, "profile_arn")) if include_direct else None
        )
        kirox_token = _nonempty_string(environ.get("KIROX_TOKEN")) if include_environment else None
        kirox_profile = (
            _nonempty_string(environ.get("KIROX_PROFILE_ARN")) if include_environment else None
        )

        if explicit_token is not None:
            return cls._from_values(explicit_token, explicit_profile, "explicit")

        # load_config() overlays KIROX auth fields independently. Treat exact
        # duplicates as environment provenance so a partial overlay cannot form
        # a mixed config/environment credential bundle.
        config_token_is_kirox = kirox_token is not None and config_token == kirox_token
        config_profile_is_kirox = kirox_profile is not None and config_profile == kirox_profile
        if config_token is not None and not config_token_is_kirox:
            trusted_config_profile = None if config_profile_is_kirox else config_profile
            return cls._from_values(config_token, trusted_config_profile, "config")

        if include_environment:
            for prefix in _ENV_PREFIXES:
                environment_token = _nonempty_string(environ.get(f"{prefix}_TOKEN"))
                if environment_token is not None:
                    environment_profile = _nonempty_string(environ.get(f"{prefix}_PROFILE_ARN"))
                    return cls._from_values(
                        environment_token,
                        environment_profile,
                        f"environment:{prefix}",
                    )

        if include_cli_db:
            configured_db_path = cls._configured_db_path(
                db_path=db_path,
                config=config if include_direct else None,
                environ=environ if include_environment else {},
            )
            if configured_db_path is not None:
                return cls._read_sqlite(configured_db_path, source="cli-db:configured")

            for candidate in cls._known_cli_db_paths():
                if not candidate.is_file():
                    continue
                try:
                    return cls._read_sqlite(candidate, source="cli-db:fixed")
                except AuthenticationError:
                    continue

        if include_environment and include_cli_db:
            raise AuthenticationError(
                "No credentials found. Set KIROX_TOKEN or ASSISTANT_TOKEN, or log in with kiro-cli."
            )
        if include_environment:
            raise AuthenticationError("No token in environment")
        raise AuthenticationError("No credential-bearing kiro-cli database found")

    @classmethod
    def _from_values(
        cls,
        token: str,
        profile_arn: Optional[str],
        source: str,
    ) -> AuthManager:
        return cls(token=token, profile_arn=profile_arn, source=source)

    @staticmethod
    def _configured_db_path(
        *,
        db_path: Optional[str | Path],
        config: object | None,
        environ: Mapping[str, str],
    ) -> Optional[Path]:
        candidates: tuple[object | None, ...] = (
            db_path,
            _config_value(config, "db_path"),
            environ.get("KIROX_DB_PATH"),
            environ.get("ASSISTANT_DB_PATH"),
        )
        for candidate in candidates:
            if isinstance(candidate, Path):
                return candidate.expanduser()
            candidate_string = _nonempty_string(candidate)
            if candidate_string is not None:
                return Path(candidate_string).expanduser()
        return None

    @staticmethod
    def _known_cli_db_paths() -> tuple[Path, ...]:
        home = Path.home()
        return (
            home / "AppData" / "Local" / "Kiro-Cli" / "data.sqlite3",
            home / "AppData" / "Local" / "kiro-cli" / "data.sqlite3",
            home / "AppData" / "Local" / "Kiro" / "data.sqlite3",
            home / ".kiro" / "data.sqlite3",
            home / ".config" / "kiro" / "data.sqlite3",
            home / ".local" / "share" / "kiro" / "data.sqlite3",
            home / "Library" / "Application Support" / "Kiro" / "data.sqlite3",
            home / "Library" / "Application Support" / "kiro-cli" / "data.sqlite3",
        )

    @classmethod
    def _read_sqlite(cls, db_path: Path, *, source: str = "cli-db") -> AuthManager:
        """Read a token from supported CLI tables without exposing row values."""
        if not db_path.is_file():
            raise AuthenticationError("Credential database not found")

        try:
            connection = sqlite3.connect(str(db_path))
        except sqlite3.Error:
            raise AuthenticationError("Unable to open credential database") from None

        try:
            auth_rows = cls._optional_query(
                connection,
                "SELECT key, value FROM auth_kv ORDER BY key",
            )
            state_rows = cls._optional_query(
                connection,
                "SELECT key, value FROM state ORDER BY key",
            )
            secret_rows = cls._optional_query(
                connection,
                "SELECT name, secret FROM secrets ORDER BY name",
            )

            token: Optional[str] = None
            profile_arn: Optional[str] = None
            for key, value in auth_rows:
                key_string = _nonempty_string(key)
                if key_string is None:
                    continue
                if token is None:
                    token = cls._token_from_db_value(key_string, value)
                if profile_arn is None:
                    profile_arn = cls._profile_from_db_value(key_string, value)

            if profile_arn is None:
                for key, value in state_rows:
                    key_string = _nonempty_string(key)
                    if key_string is None:
                        continue
                    profile_arn = cls._profile_from_db_value(key_string, value)
                    if profile_arn is not None:
                        break

            if token is None:
                for name, secret in secret_rows:
                    name_string = _nonempty_string(name) or ""
                    secret_string = _nonempty_string(secret)
                    if secret_string is None:
                        continue
                    lowered_name = name_string.lower()
                    # A refresh token is never a bearer token, and the value
                    # prefix must not override that: an `aoa`-prefixed refresh
                    # secret would be sent as the Authorization token and fail
                    # every upstream call. A name that also says `access`
                    # describes an access token held in a refreshable session,
                    # so it stays eligible.
                    if "refresh" in lowered_name and "access" not in lowered_name:
                        continue
                    if secret_string.startswith("aoa") or "token" in lowered_name:
                        token = secret_string
                        break
        except sqlite3.Error:
            raise AuthenticationError("Credential database is malformed or unreadable") from None
        finally:
            connection.close()

        if token is None:
            raise AuthenticationError("No token in credential database")
        return cls(token=token, profile_arn=profile_arn, source=source)

    @staticmethod
    def _optional_query(
        connection: sqlite3.Connection,
        query: str,
    ) -> Sequence[tuple[Any, ...]]:
        try:
            return connection.execute(query).fetchall()
        except sqlite3.OperationalError:
            return ()

    @staticmethod
    def _decoded_db_value(value: object) -> object | None:
        text = _nonempty_string(value)
        if text is None:
            return None
        if text.lstrip().startswith(("{", "[", '"')):
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return None
        return text

    @classmethod
    def _token_from_db_value(cls, key: str, value: object) -> Optional[str]:
        decoded = cls._decoded_db_value(value)
        if isinstance(decoded, Mapping):
            for field in ("access_token", "accessToken", "token"):
                token = _nonempty_string(decoded.get(field))
                if token is not None:
                    return token
            return None
        if "token" not in key.lower() or "refresh" in key.lower():
            return None
        return _nonempty_string(decoded)

    @classmethod
    def _profile_from_db_value(cls, key: str, value: object) -> Optional[str]:
        if "profile" not in key.lower():
            return None
        decoded = cls._decoded_db_value(value)
        if isinstance(decoded, Mapping):
            for field in ("arn", "profile_arn", "profileArn"):
                profile_arn = _nonempty_string(decoded.get(field))
                if profile_arn is not None:
                    return profile_arn
            return None
        return _nonempty_string(decoded)

    @property
    def is_authenticated(self) -> bool:
        return self._token is not None

    @property
    def profile_arn(self) -> Optional[str]:
        """Return the resolved profile ARN, which is not a secret."""
        return self._profile_arn

    @property
    def source(self) -> str:
        return self._source

    def get_headers(self) -> dict[str, str]:
        """Get headers for authenticated API requests."""
        headers = {"Content-Type": "application/x-amz-json-1.0"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if self._profile_arn:
            headers["x-amzn-profile-arn"] = self._profile_arn
        return headers

    def __repr__(self) -> str:
        token_mask = "***" if self._token else "None"
        return f"AuthManager(token={token_mask}, source={self._source!r})"

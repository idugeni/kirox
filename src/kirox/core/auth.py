"""Authentication."""

from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import Optional
from kirox.core.errors import AuthenticationError


class AuthManager:
    def __init__(self, token: Optional[str] = None, profile_arn: Optional[str] = None):
        self._token = token
        self._profile_arn = profile_arn

    @classmethod
    def from_env(cls) -> AuthManager:
        import os
        token = os.environ.get("ASSISTANT_TOKEN") or os.environ.get("KURO_TOKEN")
        if not token:
            raise AuthenticationError("Set KURO_TOKEN or ASSISTANT_TOKEN env var")
        return cls(token=token, profile_arn=os.environ.get("KURO_PROFILE_ARN") or os.environ.get("ASSISTANT_PROFILE_ARN"))

    @classmethod
    def from_cli_db(cls, db_path: Optional[str | Path] = None) -> AuthManager:
        if db_path:
            return cls._read_sqlite(Path(db_path))
        home = Path.home()
        for p in [
            home / "AppData/Local/Kiro-Cli/data.sqlite3",
            home / "AppData/Local/kiro-cli/data.sqlite3",
            home / ".kiro/data.sqlite3",
        ]:
            if p.exists():
                try:
                    return cls._read_sqlite(p)
                except Exception:
                    continue
        raise AuthenticationError("No credential database found")

    @classmethod
    def _read_sqlite(cls, db_path: Path) -> AuthManager:
        conn = sqlite3.connect(str(db_path))
        try:
            cursor = conn.cursor()
            token, profile_arn = None, None
            try:
                for key, value in cursor.execute("SELECT key, value FROM auth_kv").fetchall():
                    if "token" in key.lower():
                        try:
                            data = json.loads(value)
                            token = data.get("access_token", value)
                        except (json.JSONDecodeError, TypeError):
                            token = value
                    if "profile" in key.lower():
                        try:
                            data = json.loads(value)
                            profile_arn = data.get("arn", value)
                        except (json.JSONDecodeError, TypeError):
                            profile_arn = value
            except sqlite3.OperationalError:
                pass
            if not profile_arn:
                try:
                    for key, value in cursor.execute("SELECT key, value FROM state").fetchall():
                        if "profile" in key.lower():
                            try:
                                data = json.loads(value)
                                profile_arn = data.get("arn", value)
                            except (json.JSONDecodeError, TypeError):
                                profile_arn = value
                except sqlite3.OperationalError:
                    pass
            if not token:
                raise AuthenticationError("No token in database")
            return cls(token=token, profile_arn=profile_arn)
        finally:
            conn.close()

    @property
    def is_authenticated(self) -> bool:
        return self._token is not None

    def get_headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/x-amz-json-1.0"}
        if self._token: h["Authorization"] = f"Bearer {self._token}"
        if self._profile_arn: h["x-amzn-profile-arn"] = self._profile_arn
        return h

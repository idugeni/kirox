"""Authentication — auto-detect credentials."""

from __future__ import annotations
import json
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Optional
from kirox.core.errors import AuthenticationError


class AuthManager:
    """Auto-detect and manage credentials."""
    
    def __init__(self, token: Optional[str] = None, profile_arn: Optional[str] = None):
        self._token = token
        self._profile_arn = profile_arn
        self._source: str = "unknown"

    @classmethod
    def auto_detect(cls) -> AuthManager:
        """Auto-detect credentials from all sources."""
        # 1. Try environment variables first
        try:
            auth = cls.from_env()
            auth._source = "environment"
            return auth
        except AuthenticationError:
            pass

        # 2. Try kiro-cli database
        try:
            auth = cls.from_cli_db()
            auth._source = "kiro-cli"
            return auth
        except AuthenticationError:
            pass

        # 3. Try to extract from kiro-cli process
        try:
            auth = cls._from_process()
            auth._source = "process"
            return auth
        except Exception:
            pass

        # 4. Try to find any SQLite database with tokens
        try:
            auth = cls._scan_databases()
            auth._source = "scan"
            return auth
        except AuthenticationError:
            pass

        raise AuthenticationError(
            "No credentials found. Please:\n"
            "  1. Install kiro-cli: pip install kiro-cli\n"
            "  2. Login: kiro-cli login\n"
            "  3. Or set env: export KURO_TOKEN=your-token"
        )

    @classmethod
    def from_env(cls) -> AuthManager:
        """Load from environment variables."""
        token = (
            os.environ.get("KURO_TOKEN") or
            os.environ.get("ASSISTANT_TOKEN") or
            os.environ.get("OPENAI_API_KEY")  # Some tools use this
        )
        if not token:
            raise AuthenticationError("No token in environment")
        
        profile_arn = (
            os.environ.get("KURO_PROFILE_ARN") or
            os.environ.get("ASSISTANT_PROFILE_ARN")
        )
        return cls(token=token, profile_arn=profile_arn)

    @classmethod
    def from_cli_db(cls, db_path: Optional[str | Path] = None) -> AuthManager:
        """Load from kiro-cli database."""
        if db_path:
            return cls._read_sqlite(Path(db_path))

        # Auto-detect database location
        home = Path.home()
        candidates = [
            # Windows
            home / "AppData" / "Local" / "Kiro-Cli" / "data.sqlite3",
            home / "AppData" / "Local" / "kiro-cli" / "data.sqlite3",
            home / "AppData" / "Local" / "Kiro" / "data.sqlite3",
            # Linux
            home / ".kiro" / "data.sqlite3",
            home / ".config" / "kiro" / "data.sqlite3",
            home / ".local" / "share" / "kiro" / "data.sqlite3",
            # macOS
            home / "Library" / "Application Support" / "Kiro" / "data.sqlite3",
            home / "Library" / "Application Support" / "kiro-cli" / "data.sqlite3",
        ]

        for p in candidates:
            if p.exists():
                try:
                    auth = cls._read_sqlite(p)
                    auth._source = str(p)
                    return auth
                except Exception:
                    continue

        raise AuthenticationError("No kiro-cli database found")

    @classmethod
    def _from_process(cls) -> AuthManager:
        """Try to extract credentials from running kiro-cli process."""
        try:
            # Check if kiro-cli is running
            result = subprocess.run(
                ["tasklist" if os.name == "nt" else "ps", "aux"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if "kiro" in result.stdout.lower():
                # Try to find the database from process
                # This is a fallback - not always reliable
                pass
        except Exception:
            pass
        raise AuthenticationError("Could not extract from process")

    @classmethod
    def _scan_databases(cls) -> AuthManager:
        """Scan common locations for any SQLite database with tokens."""
        home = Path.home()
        
        # Common database locations
        search_dirs = [
            home / "AppData" / "Local",
            home / ".config",
            home / ".local" / "share",
        ]
        
        for search_dir in search_dirs:
            if not search_dir.exists():
                continue
            
            try:
                # Find all .sqlite3 files
                for db_path in search_dir.rglob("*.sqlite3"):
                    try:
                        auth = cls._read_sqlite(db_path)
                        if auth.is_authenticated:
                            auth._source = str(db_path)
                            return auth
                    except Exception:
                        continue
            except Exception:
                continue

        raise AuthenticationError("No credentials found in any database")

    @classmethod
    def _read_sqlite(cls, db_path: Path) -> AuthManager:
        """Read token from SQLite database."""
        conn = sqlite3.connect(str(db_path))
        try:
            cursor = conn.cursor()
            token, profile_arn = None, None

            # Try auth_kv table
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

            # Try state table
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

            # Try secrets table
            if not token:
                try:
                    for name, secret in cursor.execute("SELECT name, secret FROM secrets").fetchall():
                        if secret and secret.startswith("aoa"):
                            token = secret
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

    @property
    def source(self) -> str:
        return self._source

    def get_headers(self) -> dict[str, str]:
        """Get headers for API requests."""
        h = {"Content-Type": "application/x-amz-json-1.0"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        if self._profile_arn:
            h["x-amzn-profile-arn"] = self._profile_arn
        return h

    def __repr__(self) -> str:
        token_mask = "***" if self._token else "None"
        return f"AuthManager(token={token_mask}, source={self._source})"

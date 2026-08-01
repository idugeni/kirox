"""Tests for deterministic authentication resolution."""

import json
import sqlite3
from pathlib import Path

import pytest

from kirox.core.auth import AuthManager
from kirox.core.errors import AuthenticationError
from kirox.utils.config import Config, load_config


def create_auth_db(path: Path, token: str = "db-token", profile: str = "db-profile") -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE auth_kv (key TEXT, value TEXT)")
        connection.executemany(
            "INSERT INTO auth_kv (key, value) VALUES (?, ?)",
            [
                ("access_token", json.dumps({"access_token": token})),
                ("profile", json.dumps({"arn": profile})),
            ],
        )
        connection.commit()
    finally:
        connection.close()


def bearer_token(auth: AuthManager) -> str:
    return auth.get_headers()["Authorization"].removeprefix("Bearer ")


def test_headers_and_secret_redaction(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    token = "dummy-token-secret"
    profile = "dummy-profile-secret"
    auth = AuthManager.resolve(token=token, profile_arn=profile, environ={})
    malformed = tmp_path / "malformed.sqlite3"
    malformed.write_text(f"{token} {profile}", encoding="utf-8")

    with pytest.raises(AuthenticationError) as error:
        AuthManager.from_cli_db(malformed)

    assert auth.get_headers()["Authorization"] == f"Bearer {token}"
    assert auth.source == "explicit"
    assert "***" in repr(auth)
    exposed = "\n".join((repr(auth), auth.source, str(error.value), caplog.text))
    assert token not in exposed
    assert profile not in exposed


def test_resolver_precedence_uses_same_tier_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_db = tmp_path / "configured.sqlite3"
    fixed_db = tmp_path / "fixed.sqlite3"
    create_auth_db(configured_db, "configured-db-token", "configured-db-profile")
    create_auth_db(fixed_db, "fixed-db-token", "fixed-db-profile")
    monkeypatch.setattr(AuthManager, "_known_cli_db_paths", staticmethod(lambda: (fixed_db,)))
    environment = {
        "KIROX_TOKEN": "kirox-token",
        "KIROX_PROFILE_ARN": "kirox-profile",
        "ASSISTANT_TOKEN": "assistant-token",
        "ASSISTANT_PROFILE_ARN": "assistant-profile",
    }
    config = Config(
        token="config-token",
        profile_arn="config-profile",
        db_path=str(configured_db),
    )

    resolved = [
        AuthManager.resolve(
            token="explicit-token",
            profile_arn="explicit-profile",
            config=config,
            environ=environment,
        ),
        AuthManager.resolve(config=config, environ=environment),
        AuthManager.resolve(db_path=configured_db, environ=environment),
        AuthManager.resolve(
            db_path=configured_db,
            environ={
                "ASSISTANT_TOKEN": "assistant-token",
                "ASSISTANT_PROFILE_ARN": "assistant-profile",
            },
        ),
        AuthManager.resolve(db_path=configured_db, environ={}),
        AuthManager.resolve(environ={}),
    ]

    assert [
        (bearer_token(auth), auth.get_headers().get("x-amzn-profile-arn")) for auth in resolved
    ] == [
        ("explicit-token", "explicit-profile"),
        ("config-token", "config-profile"),
        ("kirox-token", "kirox-profile"),
        ("assistant-token", "assistant-profile"),
        ("configured-db-token", "configured-db-profile"),
        ("fixed-db-token", "fixed-db-profile"),
    ]
    assert resolved[-2].source == "cli-db:configured"
    assert resolved[-1].source == "cli-db:fixed"


def test_loaded_config_does_not_cross_pair_environment_overlays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = (
        (
            {"token": "file-token"},
            {"KIROX_PROFILE_ARN": "environment-profile"},
            "file-token",
        ),
        (
            {"profile_arn": "file-profile"},
            {"KIROX_TOKEN": "environment-token"},
            "environment-token",
        ),
    )
    environment_names = (
        "KIROX_TOKEN",
        "KIROX_PROFILE_ARN",
        "KIROX_DB_PATH",
        "ASSISTANT_TOKEN",
        "ASSISTANT_PROFILE_ARN",
        "ASSISTANT_DB_PATH",
    )

    for file_config, environment, expected_token in cases:
        for name in environment_names:
            monkeypatch.delenv(name, raising=False)
        for name, value in environment.items():
            monkeypatch.setenv(name, value)

        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(file_config), encoding="utf-8")
        auth = AuthManager.resolve(config=load_config(config_path))

        assert bearer_token(auth) == expected_token
        assert "x-amzn-profile-arn" not in auth.get_headers()


def test_configured_database_path_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_names = ("argument", "config", "kirox-environment", "assistant-environment")
    databases = {name: tmp_path / f"{name}.sqlite3" for name in database_names}
    for name, path in databases.items():
        create_auth_db(path, f"{name}-token", f"{name}-profile")
    monkeypatch.setattr(AuthManager, "_known_cli_db_paths", staticmethod(lambda: ()))

    config = Config(db_path=str(databases["config"]))
    environment = {
        "KIROX_DB_PATH": str(databases["kirox-environment"]),
        "ASSISTANT_DB_PATH": str(databases["assistant-environment"]),
    }
    resolved = (
        AuthManager.resolve(
            db_path=databases["argument"],
            config=config,
            environ=environment,
        ),
        AuthManager.resolve(config=config, environ=environment),
        AuthManager.resolve(config=Config(), environ=environment),
        AuthManager.resolve(
            config=Config(),
            environ={"ASSISTANT_DB_PATH": environment["ASSISTANT_DB_PATH"]},
        ),
    )

    assert [
        (bearer_token(auth), auth.get_headers().get("x-amzn-profile-arn")) for auth in resolved
    ] == [
        ("argument-token", "argument-profile"),
        ("config-token", "config-profile"),
        ("kirox-environment-token", "kirox-environment-profile"),
        ("assistant-environment-token", "assistant-environment-profile"),
    ]
    assert all(auth.source == "cli-db:configured" for auth in resolved)


def test_orphan_profiles_do_not_cross_credential_tiers(tmp_path: Path) -> None:
    db_path = tmp_path / "auth.sqlite3"
    create_auth_db(db_path)

    resolved = [
        AuthManager.resolve(
            profile_arn="orphan-explicit-profile",
            config=Config(token="config-token"),
            environ={},
        ),
        AuthManager.resolve(
            config=Config(profile_arn="orphan-config-profile"),
            environ={"KIROX_TOKEN": "kirox-token"},
        ),
        AuthManager.resolve(
            environ={
                "KIROX_TOKEN": "kirox-token",
                "ASSISTANT_PROFILE_ARN": "orphan-assistant-profile",
            }
        ),
        AuthManager.resolve(
            environ={
                "KIROX_PROFILE_ARN": "orphan-kirox-profile",
                "ASSISTANT_TOKEN": "assistant-token",
            }
        ),
        AuthManager.resolve(
            db_path=db_path,
            environ={
                "KIROX_PROFILE_ARN": "orphan-kirox-profile",
                "ASSISTANT_PROFILE_ARN": "orphan-assistant-profile",
            },
        ),
    ]

    assert [bearer_token(auth) for auth in resolved] == [
        "config-token",
        "kirox-token",
        "kirox-token",
        "assistant-token",
        "db-token",
    ]
    assert all("x-amzn-profile-arn" not in auth.get_headers() for auth in resolved[:-1])
    assert resolved[-1].get_headers()["x-amzn-profile-arn"] == "db-profile"


def test_openai_key_is_not_an_auth_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AuthManager, "_known_cli_db_paths", staticmethod(lambda: ()))

    with pytest.raises(AuthenticationError, match="No credentials found"):
        AuthManager.resolve(environ={"OPENAI_API_KEY": "must-not-be-used"})


def test_malformed_and_tokenless_databases_are_rejected(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.sqlite3"
    malformed.write_bytes(b"not a sqlite database or a secret")
    tokenless = tmp_path / "tokenless.sqlite3"
    sqlite3.connect(tokenless).close()

    with pytest.raises(AuthenticationError, match="malformed or unreadable") as error:
        AuthManager.from_cli_db(malformed)
    assert "secret" not in str(error.value)

    with pytest.raises(AuthenticationError, match="No token"):
        AuthManager.from_cli_db(tokenless)


def test_missing_explicit_database_does_not_create_a_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite3"

    with pytest.raises(AuthenticationError, match="not found"):
        AuthManager.from_cli_db(missing)

    assert not missing.exists()


def test_legacy_environment_and_auto_apis_delegate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KIROX_TOKEN", "kirox-token")
    monkeypatch.setenv("ASSISTANT_TOKEN", "assistant-token")
    monkeypatch.setenv("OPENAI_API_KEY", "ignored-token")

    assert bearer_token(AuthManager.from_env()) == "kirox-token"
    assert bearer_token(AuthManager.auto_detect()) == "kirox-token"

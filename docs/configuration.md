# Configuration

## Configuration file

Kirox loads JSON from `~/.kirox/config.json` by default, or from `KIROX_CONFIG` when that variable is set.

```json
{
  "region": "us-east-1",
  "server_host": "127.0.0.1",
  "server_port": 8420,
  "auto_refresh": true,
  "refresh_interval": 3000,
  "log_level": "INFO",
  "log_file": null,
  "token": null,
  "profile_arn": null,
  "db_path": null
}
```

| Option | Type | Default | Contract |
|---|---:|---:|---|
| `region` | string | `us-east-1` | Upstream runtime/management region; non-empty |
| `server_host` | string | `127.0.0.1` | Must resolve syntactically to loopback (`127.0.0.0/8`, `::1`, or `localhost`) |
| `server_port` | integer | `8420` | `0` chooses an ephemeral port; otherwise `1`–`65535` |
| `auto_refresh` | boolean | `true` | Enable periodic credential health checks |
| `refresh_interval` | integer | `3000` | Positive seconds between checks; waits are interruptible |
| `log_level` | string | `INFO` | `CRITICAL`, `ERROR`, `WARNING`, `INFO`, `DEBUG`, or `NOTSET`; the legacy aliases `WARN` and `FATAL` are accepted and normalized, and case is normalized |
| `log_file` | string/null | `null` | Optional expanded path used by service/CLI logging |
| `token` | string/null | `null` | Configured bearer token |
| `profile_arn` | string/null | `null` | Optional configured profile ARN |
| `db_path` | string/null | `null` | Explicit supported CLI SQLite database |

A non-loopback `server_host`, including `0.0.0.0`, is rejected. Kirox is intentionally local-only; there is no supported public-bind mode.

## Validation and persistence

Every value is validated when a `Config` is constructed, loaded from a file, or overlaid from the environment. A violation raises `ConfigError`, a `ValueError` subclass that names the offending field in both its message and its `field_name` attribute, so a bad config fails at load time instead of surfacing later as an obscure bind or logging failure. A malformed or non-UTF-8 file is reported the same way with `field_name` `"body"`, so `except ConfigError` covers every load failure. Unknown keys are ignored so a newer config file stays loadable by an older Kirox.

An empty or whitespace-only string in the file is a validation error rather than a silent no-op, because in a file it is almost always a mistake. Environment overlays keep the older behavior: an empty variable overlays nothing. String fields are stripped, so a padded `region` cannot produce a malformed upstream URL.

Files are read and written as UTF-8 regardless of platform locale. `Config.to_file()` writes to a temporary file in the same directory, requests owner-only permissions where the platform supports them, and then replaces the target in one step, so an interrupted write cannot leave a truncated configuration and a persisted `token` is not left group- or world-readable. Two consequences are worth knowing: replacing the file resets its permissions, so permissions set by hand do not survive a write, and a write interrupted by a process kill can leave a private temporary sibling, which the next write removes. On Windows `os.chmod` cannot express owner-only access, so the written file inherits directory ACLs.

## Deterministic authentication precedence

`AuthManager.resolve()` selects one credential bundle in this public order:

1. Explicit `token` and explicit `profile_arn` method arguments
2. Configuration `token` and configuration `profile_arn` values
3. `KIROX_TOKEN` and `KIROX_PROFILE_ARN` environment variables
4. `ASSISTANT_TOKEN` and `ASSISTANT_PROFILE_ARN` backward-compatible aliases
5. A configured database path (`db_path` argument > `config.db_path` > `KIROX_DB_PATH` > `ASSISTANT_DB_PATH`)
6. Known fixed CLI database locations

A profile ARN is used only when it comes from the same tier as the selected token. Profiles without a token in their own tier are orphaned and ignored; they never supplement another environment tier, configuration, explicit arguments, or a database token. A database token uses only the profile read from that same database file. Because `load_config()` overlays `KIROX_*` auth fields independently, exact config/environment duplicates are retained as KIROX provenance so a one-field overlay cannot create a mixed bundle. Empty or whitespace-only strings are ignored. Missing, malformed, or tokenless configured databases fail without being created or exposing database paths or contents in errors.

The client factory methods have narrower intentional scopes:

- `AssistantClient.auto()` / `AuthManager.auto_detect()` use environment then supported CLI databases.
- `AssistantClient.from_env()` uses `KIROX_*` then `ASSISTANT_*` only.
- `AssistantClient.from_cli_db()` uses an explicit or fixed supported database only.
- The managed service and MCP command load configuration before calling the deterministic resolver.

## Environment variables

| Variable | Purpose |
|---|---|
| `KIROX_CONFIG` | Override the configuration file path |
| `KIROX_TOKEN` | Preferred bearer token; also overlays the loaded config |
| `KIROX_PROFILE_ARN` | Preferred profile ARN; also overlays the loaded config |
| `KIROX_REGION` | Overlays the configured region |
| `KIROX_SERVER_HOST` | Overlays the configured loopback bind host |
| `KIROX_SERVER_PORT` | Overlays the configured port; must be an integer in range |
| `KIROX_LOG_LEVEL` | Overlays the configured log level |
| `KIROX_LOG_FILE` | Overlays the configured log file path |
| `KIROX_DB_PATH` | Preferred explicit CLI database path |
| `KIROX_STATE_FILE` | Override the managed service state path |
| `ASSISTANT_TOKEN` | Backward-compatible token alias |
| `ASSISTANT_PROFILE_ARN` | Backward-compatible profile alias |
| `ASSISTANT_DB_PATH` | Backward-compatible database alias |

An empty or whitespace-only value does not overlay anything, and every overlaid value goes through the same validation as a file value.

`OPENAI_API_KEY` is intentionally ignored for upstream authentication. Downstream OpenAI-compatible clients may still require any placeholder value in their own configuration; Kirox does not consume it.

## Programmatic configuration

```python
from pathlib import Path

from kirox.utils.config import Config, load_config

config = load_config()
custom = Config(region="eu-west-1", server_port=9000)
custom.to_file(Path.home() / ".kirox" / "config.json")
```

For explicit in-process credentials:

```python
from kirox.core.auth import AuthManager

credentials = AuthManager.resolve(
    token="token-from-secure-storage",
    profile_arn="profile-arn",
)
```

Do not log `get_headers()` or persist a control token outside Kirox state.

## Service state and shutdown

While running, Kirox writes validated ownership data to `~/.kirox/service.json` (or `KIROX_STATE_FILE`). It contains the PID, loopback URL, start time, and a random control token. Writes are atomic and owner-only permissions are requested where supported.

`kirox stop` sends the token to an internal loopback-only endpoint and waits for graceful teardown. State is deleted only if it still belongs to the same service instance. `--force` refuses invalid/current PIDs and ownership changes, and terminates a PID only when its kernel-derived process identity still matches the identity recorded at startup. It fails closed when state predates identity recording, when the identity no longer matches a reused PID, and on platforms without a stable identity-bound termination handle such as macOS.

## Refresh scheduler

When `auto_refresh` is enabled, the scheduler checks authentication by listing models. An HTTP 401 or 403 from that check, or an error indicating an expired or invalid token, triggers deterministic database re-resolution and atomic client auth replacement, followed by a single bounded retry of the check. Other failures are reported and retried after an interruptible backoff. Shutdown does not wait for a full refresh interval.

## Logging

Use `DEBUG`, `INFO`, `WARNING`, or `ERROR`. The CLI override is attached to the `run` subcommand:

```bash
kirox run --verbose
```

To write service logs:

```json
{
  "log_level": "DEBUG",
  "log_file": "~/.kirox/kirox.log"
}
```

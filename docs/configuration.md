# Configuration

## Configuration file

Kirox loads JSON from `~/.kirox/config.json` by default.

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
| `region` | string | `us-east-1` | Upstream runtime/management region |
| `server_host` | string | `127.0.0.1` | Must resolve syntactically to loopback (`127.0.0.0/8`, `::1`, or `localhost`) |
| `server_port` | integer | `8420` | `0` chooses an ephemeral port; otherwise `1`–`65535` |
| `auto_refresh` | boolean | `true` | Enable periodic credential health checks |
| `refresh_interval` | integer | `3000` | Seconds between checks; waits are interruptible |
| `log_level` | string | `INFO` | Python logging level |
| `log_file` | string/null | `null` | Optional expanded path used by service/CLI logging |
| `token` | string/null | `null` | Configured bearer token |
| `profile_arn` | string/null | `null` | Optional configured profile ARN |
| `db_path` | string/null | `null` | Explicit supported CLI SQLite database |

A non-loopback `server_host`, including `0.0.0.0`, is rejected. Kirox is intentionally local-only; there is no supported public-bind mode.

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
| `KIROX_TOKEN` | Preferred bearer token; also overlays the loaded config |
| `KIROX_PROFILE_ARN` | Preferred profile ARN; also overlays the loaded config |
| `KIROX_REGION` | Overlays the configured region |
| `KIROX_DB_PATH` | Preferred explicit CLI database path |
| `KIROX_STATE_FILE` | Override the managed service state path |
| `ASSISTANT_TOKEN` | Backward-compatible token alias |
| `ASSISTANT_PROFILE_ARN` | Backward-compatible profile alias |
| `ASSISTANT_DB_PATH` | Backward-compatible database alias |

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

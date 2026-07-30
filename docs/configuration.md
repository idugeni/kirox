# Configuration

## Configuration File

Kirox uses a JSON configuration file located at `~/.kirox/config.json`.

### Default Configuration

```json
{
  "region": "us-east-1",
  "server_port": 8420,
  "server_host": "127.0.0.1",
  "auto_refresh": true,
  "refresh_interval": 3000,
  "log_level": "INFO",
  "log_file": null
}
```

## Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `region` | string | `us-east-1` | AWS region for Kiro API |
| `server_port` | int | `8420` | Local HTTP server port |
| `server_host` | string | `127.0.0.1` | Local HTTP server host |
| `auto_refresh` | bool | `true` | Auto-refresh token before expiry |
| `refresh_interval` | int | `3000` | Token refresh interval (seconds) |
| `log_level` | string | `INFO` | Logging level |
| `log_file` | string | `null` | Log file path |
| `token` | string | `null` | Bearer token (overrides DB) |
| `profile_arn` | string | `null` | AWS profile ARN |
| `db_path` | string | `null` | Path to kiro-cli database |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `KIROX_TOKEN` | Bearer token |
| `KIROX_PROFILE_ARN` | AWS profile ARN |
| `KIROX_REGION` | AWS region |

## Programmatic Configuration

```python
from kirox.utils.config import Config, load_config

# Load from file
config = load_config()

# Create custom config
config = Config(
    region="eu-west-1",
    server_port=9000,
    auto_refresh=True,
)

# Save to file
config.to_file(Path("~/.kirox/config.json"))
```

## Server Configuration

The local HTTP server runs on `127.0.0.1:8420` by default. To change:

```json
{
  "server_host": "0.0.0.0",
  "server_port": 9000
}
```

**Note**: Binding to `0.0.0.0` exposes the server to your network. Only do this if needed.

## Token Refresh

Kirox automatically refreshes your token before it expires. To configure:

```json
{
  "auto_refresh": true,
  "refresh_interval": 3000
}
```

Set `auto_refresh: false` to disable automatic refresh.

## Logging

### Log Levels

- `DEBUG` - Detailed information
- `INFO` - General information (default)
- `WARNING` - Warnings only
- `ERROR` - Errors only

### Log to File

```json
{
  "log_level": "DEBUG",
  "log_file": "~/.kirox/kirox.log"
}
```

### Verbose Mode

```bash
kirox -v              # Verbose output
kirox run --verbose   # Same as above
```

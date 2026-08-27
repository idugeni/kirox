# Kirox

Kirox is a typed Python SDK and local HTTP bridge for AI coding-assistant workflows. It provides incremental AWS EventStream decoding, a synchronous client, managed local service and tray lifecycle, text-only OpenAI/Anthropic-compatible endpoints, and an optional MCP stdio server.

Kirox deliberately binds its HTTP service to loopback addresses only. It is not a network-facing gateway and rejects `0.0.0.0` or other non-loopback hosts.

## Requirements

- Python 3.10 or newer; tested on 3.10–3.14
- A Kiro-compatible bearer token or an authenticated supported CLI database
- Windows, macOS, or Linux; tray support depends on the desktop environment

## Installation

```bash
python -m pip install kirox                 # SDK, CLI, and local HTTP bridge
python -m pip install "kirox[service]"      # Add pystray and Pillow
python -m pip install "kirox[mcp]"          # Add the MCP SDK
python -m pip install "kirox[service,mcp]"  # All optional features
```

The MCP dependency is optional and imported only by MCP operations. Running `kirox-mcp` without it exits with an instruction to install `kirox[mcp]`; importing `kirox.mcp.server` remains safe.

## Authentication

`AuthManager.resolve()` uses a deterministic precedence order:

1. Explicit `token` / `profile_arn` arguments
2. Configuration values
3. `KIROX_TOKEN` / `KIROX_PROFILE_ARN`
4. Backward-compatible `ASSISTANT_TOKEN` / `ASSISTANT_PROFILE_ARN` aliases
5. An explicitly configured database, then fixed supported CLI database locations

`KIROX_DB_PATH` and `ASSISTANT_DB_PATH` can select a database. Kirox does not scan arbitrary files or treat unrelated keys such as `OPENAI_API_KEY` as upstream credentials. A secret whose name marks it as a refresh token is never used as a bearer token, whatever its value looks like.

## CLI and managed lifecycle

```bash
kirox                         # Same as `kirox run`
kirox run --no-tray           # Service only
kirox run --no-update         # Disable the background PyPI update check
kirox status
kirox stop                    # Authenticated graceful shutdown
kirox stop --force            # Identity-verified PID fallback for unchanged state
kirox models
kirox chat --model auto
kirox ask --model auto "Hello"
kirox update
```

The service owns one client, refresh scheduler, HTTP server, state file, and optional tray. Startup rolls back partial resources; normal stop closes threads, sockets, state, and the client idempotently. Runtime control data is stored in `~/.kirox/service.json` with a per-process token and is accepted only over loopback. State also records a kernel-derived process identity, so `stop --force` terminates only a process that still matches the recorded owner and fails closed on mismatch, on missing identity, or where the platform cannot supply one.

## Python API

```python
from kirox import AssistantClient

with AssistantClient.auto() as client:
    for event in client.chat("Explain this function", model_id="auto"):
        if event.content:
            print(event.content, end="", flush=True)
```

`AssistantClient.chat()` validates and decodes upstream EventStream frames incrementally, including both CRCs and frame/header bounds. Closing the generator early closes the HTTP response.

## Local HTTP bridge

Start the service and use `http://127.0.0.1:8420` (or the configured loopback port):

```bash
kirox run --no-tray

curl http://127.0.0.1:8420/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"Hello"}],"stream":true}'
```

Available routes include:

- Native: `/health`, `/`, `/api/models`, `/api/chat`, `/api/token/status`
- OpenAI-compatible: `/v1/models`, `/v1/chat/completions`
- Anthropic-compatible: `/v1/messages`

The provider adapters are intentionally **text-only**. They accept text strings or arrays of text blocks, preserve system/user/assistant history in a canonical transcript, require the final message to be from the user, and reject unsupported fields instead of silently ignoring them. Tools, tool calls, images, audio, metadata, sampling parameters, and other multimodal semantics are not supported. `max_tokens` is validated for compatibility but not enforced; responses carry an HTTP `Warning` and do not fabricate token usage.

Streaming is incremental SSE. OpenAI streams terminate once with `[DONE]`; Anthropic streams use ordered message/content events and a single terminal event. Upstream and internal exception details are sanitized from HTTP responses.

Requests must carry a loopback `Host` header, so a browser page on another origin cannot reach the bridge through DNS rebinding, and bodies larger than 1 MiB are rejected with HTTP 413 before validation or upstream work.

See [API Reference](docs/api-reference.md) for the complete request contract.

## MCP

After installing the extra, configure an MCP client to launch the stdio command:

```json
{
  "mcpServers": {
    "kirox": {"command": "kirox-mcp"}
  }
}
```

The server exposes one `kirox_chat` tool with required non-empty `message` and optional non-empty `model` (default `auto`). Unknown tools, unknown arguments, and invalid values return errors. Synchronous client work runs in a worker thread so the MCP event loop stays responsive, and the owned client closes when the stdio session ends.

## Configuration

The default file is `~/.kirox/config.json`:

```json
{
  "region": "us-east-1",
  "server_host": "127.0.0.1",
  "server_port": 8420,
  "auto_refresh": true,
  "refresh_interval": 3000,
  "log_level": "INFO"
}
```

Only loopback values are accepted for `server_host`. Every field is validated on load, so an invalid value raises `ConfigError` naming the field instead of failing later during startup. `KIROX_CONFIG` selects a different file, and `KIROX_TOKEN`, `KIROX_PROFILE_ARN`, `KIROX_REGION`, `KIROX_SERVER_HOST`, `KIROX_SERVER_PORT`, `KIROX_LOG_LEVEL`, and `KIROX_LOG_FILE` overlay individual fields. See [Configuration](docs/configuration.md) for all fields and environment aliases.

## Development and release gates

```bash
python -m pip install -e ".[dev,mcp]"
python -m ruff format --check .
python -m ruff check .
python -m mypy src tests scripts
python -m pyrefly check
python -m pytest tests -q --cov=kirox --cov-branch --cov-report=term-missing
python -m build
python scripts/verify_distribution.py
```

Coverage is branch-aware and must remain at least 85%. Both type checkers run because they catch different classes of error; Pyrefly is configured explicitly in `pyproject.toml` so it does not silently fall back to its permissive `basic` preset. The distribution verifier checks `py.typed`, version metadata, both console scripts, then installs the wheel into a temporary clean virtual environment for import, CLI, missing-MCP, and `pip check` smoke tests.

## Documentation

- [Installation](docs/installation.md)
- [Configuration](docs/configuration.md)
- [API Reference](docs/api-reference.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## License

MIT License. See [LICENSE](LICENSE).

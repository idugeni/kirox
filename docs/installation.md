# Installation

## Supported environments

Kirox requires Python 3.10 or newer and is tested on Python 3.10–3.14. It runs on Windows, macOS, and Linux. The system tray extra requires a graphical desktop supported by `pystray`; the SDK, CLI, HTTP bridge, and MCP stdio command do not require a tray.

Authentication requires either a bearer token or a supported authenticated CLI database. Installation does not contact an upstream AI service.

## Install from PyPI

```bash
python -m pip install kirox
```

Optional features are separate so the base runtime remains small:

```bash
python -m pip install "kirox[service]"      # pystray + Pillow
python -m pip install "kirox[mcp]"          # MCP SDK
python -m pip install "kirox[service,mcp]"  # both
```

The service scheduler uses the Python standard library; APScheduler is not required.

## Install from source

```bash
git clone https://github.com/idugeni/kirox.git
cd kirox
python -m venv .venv
```

Activate the environment:

```text
Windows PowerShell: .venv\Scripts\Activate.ps1
Windows cmd.exe:    .venv\Scripts\activate.bat
POSIX shells:       source .venv/bin/activate
```

Then install the editable development environment:

```bash
python -m pip install -e ".[dev,mcp]"
```

## Verify the installation

```bash
kirox --version
kirox status
python -c "import kirox; print(kirox.__version__)"
```

For MCP:

```bash
kirox-mcp
```

`kirox-mcp` is a stdio server and normally waits for an MCP client. If the optional SDK is absent, it exits immediately with the exact `python -m pip install "kirox[mcp]"` remedy. Importing `kirox.mcp.server` does not require the extra.

## Authentication setup

Use one of the supported inputs:

```text
KIROX_TOKEN                 preferred token environment variable
KIROX_PROFILE_ARN           optional profile ARN
ASSISTANT_TOKEN             backward-compatible token alias
ASSISTANT_PROFILE_ARN       backward-compatible profile alias
KIROX_DB_PATH               explicit supported CLI database
ASSISTANT_DB_PATH           backward-compatible database alias
```

Alternatively, set `token`, `profile_arn`, or `db_path` in `~/.kirox/config.json`, or authenticate with the supported CLI so one of its fixed database locations exists. `OPENAI_API_KEY` is used by some downstream clients but is not a Kirox upstream credential.

## Service safety

The HTTP bridge defaults to `127.0.0.1:8420` and accepts only loopback bind addresses. A configuration such as `0.0.0.0` fails at startup rather than exposing the bridge to a LAN.

Use `kirox stop` for authenticated graceful shutdown. `kirox stop --force` is a fallback that targets only the PID from unchanged, fully validated local state, and only when that PID still matches the process identity recorded when the service started. Linux and Windows support identity-bound termination; on other platforms, including macOS, force stop fails closed instead of signalling a possibly reused PID.

## Development verification

Run the same checks as CI:

```bash
python -m ruff format --check .
python -m ruff check .
python -m mypy src tests scripts
python -m pyrefly check
python -m pytest tests -q --cov=kirox --cov-branch --cov-report=term-missing
python -m build
python scripts/verify_distribution.py
```

The final command creates a temporary isolated virtual environment, installs the built wheel, checks both console scripts and typed-package metadata, and removes the temporary environment automatically.

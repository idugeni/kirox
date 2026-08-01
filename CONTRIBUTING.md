# Contributing to Kirox

## Development setup

Kirox supports Python 3.10–3.14 on Windows and Linux CI.

```bash
git clone https://github.com/your-username/kirox.git
cd kirox
python -m venv .venv
```

Activate the environment with `.venv\Scripts\Activate.ps1` on Windows PowerShell or `source .venv/bin/activate` in a POSIX shell, then install all development dependencies used by the test suite:

```bash
python -m pip install -e ".[dev,mcp]"
```

The tray extra is not required for tests; tray behavior is exercised with fakes so CI does not need a desktop session.

## Required quality gates

Run every command from the repository root before opening a pull request:

```bash
python -m ruff format --check .
python -m ruff check .
python -m mypy src tests scripts
python -m pyrefly check
python -m pytest tests -q --cov=kirox --cov-branch --cov-report=term-missing
python -m build
python scripts/verify_distribution.py
```

Branch coverage must remain at least 80%. Both type checkers are required: they disagree often enough that each one catches errors the other misses. Pyrefly reads its `[tool.pyrefly]` section in `pyproject.toml`; without that section it would fall back to the `basic` preset and silence most type errors. The distribution verifier checks the wheel version, `kirox/py.typed`, `kirox` and `kirox-mcp` entry points, then performs a clean temporary-environment install/import/CLI/missing-extra smoke test and `pip check`.

CI runs the same gates on Windows and Ubuntu with Python 3.10, 3.11, 3.12, 3.13, and 3.14. A separate job installs the built wheel with the optional `service` extra and runs a headless tray-import and `pip check` smoke test, so the tray dependencies are exercised without requiring a desktop session in the main matrix. A local Python version outside that matrix is useful but does not replace supported-version CI.

## Code style and typing

- Use Ruff as the formatter and linter; do not hand-format around it.
- Type public APIs and keep `mypy src tests scripts` and `pyrefly check` clean.
- Preserve existing public imports and call signatures unless a compatibility plan is documented.
- Keep optional features lazy: importing the base package must not require MCP or tray dependencies.
- Do not add runtime dependencies when the standard library or an existing dependency is sufficient.
- `_version.py` is the single version source. Do not duplicate the package version in `pyproject.toml` or service responses.

To apply formatting during development:

```bash
python -m ruff format .
python -m ruff check .
```

## Tests

Add behavior-focused tests for every change. Tests must not use live credentials, external AI services, or arbitrary user databases.

For relevant areas, cover:

- Success, validation, and resource-cleanup paths
- Incremental streaming and malformed/truncated input
- Authentication precedence without exposing secret values
- Service startup rollback, idempotent shutdown, socket/thread cleanup, and state ownership
- Optional dependency present and absent behavior
- Provider adapter text-only rejection rather than silent semantic loss

Use `httpx.MockTransport`, Flask's test client, temporary paths, and injected fakes. Avoid assertions that only repeat implementation constants without exercising a contract.

## Documentation and changelog

Update README and focused docs whenever configuration, compatibility limitations, entry points, lifecycle behavior, or API envelopes change. Do not publish a static test-count claim; it becomes stale immediately. Add user-visible changes to `CHANGELOG.md` using semantic-versioning categories.

## Pull requests

1. Create a focused branch and make the smallest compatible change.
2. Add meaningful tests and documentation.
3. Run the full quality and distribution gates above.
4. Include supported-OS/Python limitations and any unverified behavior in the pull-request description.
5. Submit the pull request without generated temporary virtual environments or distribution-check directories.

Be respectful and do not include credentials, database contents, or user prompts in issues, fixtures, logs, or screenshots.

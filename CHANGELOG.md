# Changelog

All notable changes to Kirox are documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-07-31

### Added

- Strict incremental AWS EventStream decoder with CRC, size, header-type, UTF-8, and truncation validation.
- Deterministic credential resolution with explicit/config/KIROX/ASSISTANT/database precedence and fixed database discovery locations.
- Managed service state, authenticated loopback shutdown, startup rollback, socket/thread ownership, and idempotent client cleanup.
- Text-only OpenAI chat-completions and Anthropic messages adapters with canonical history, field-addressed validation, incremental SSE, disconnect cleanup, and sanitized errors.
- Operational `kirox-mcp` stdio entry point with lazy optional imports, strict tool input, worker-thread bridging, one client, and lifecycle cleanup.
- PEP 561 `py.typed` marker and wheel/clean-install distribution verifier.
- Windows and Ubuntu CI matrix for Python 3.10–3.14 covering Ruff, mypy, Pyrefly, branch coverage, wheel/sdist build, metadata inspection, and install smoke tests, plus a dedicated job that installs the built wheel with the optional `service` extra and runs a headless tray-import and `pip check` smoke test.
- Pyrefly as a second required type checker, with an explicit `[tool.pyrefly]` section so it does not fall back to the permissive `basic` preset, and warnings promoted to failures.
- Loopback `Host` header validation and a 1 MiB request-body limit on every bridge route, returning HTTP 400 and provider-shaped HTTP 413 responses before validation or upstream work.
- Kernel-derived process identity in service state, with identity-bound `kirox stop --force` termination on Linux and Windows that fails closed on PID reuse, missing identity, or unsupported platforms.

### Changed

- The token scheduler now treats HTTP 401 and 403 from its authentication check as credential failures, re-resolves credentials, and retries the check once instead of relying on error-message text.
- `AssistantClient.list_tools()` now validates the upstream envelope strictly and raises `APIError` for a missing `result`, a missing or non-array `tools` member, or entries without a non-empty `name` and an `inputSchema` object.
- Package version now has one source in `src/kirox/_version.py`; CLI, health responses, root metadata, and built distributions report 1.1.0.
- HTTP service binding is explicitly loopback-only.
- The service token scheduler now uses an interruptible standard-library worker, removing the unused APScheduler dependency.
- CLI, tray, scheduler, and server lifecycle paths now close owned resources deterministically.
- Quality configuration now checks formatting, imports, common bugs, repository typing with two checkers, and a minimum 80% branch-aware coverage threshold.
- Development tooling is pinned to exact versions, and runtime dependencies now carry both a tested floor and a major upper bound. The optional MCP extra stays on `mcp>=1.28.1,<2`: the v2 SDK moved the low-level `Server` handlers from decorators to constructor parameters, so the current stdio server would not run against it.
- Installation, configuration, API, contribution, and lifecycle documentation now match actual behavior and text-only limitations.

### Fixed

- EventStream parsing no longer assumes complete HTTP chunks or accepts bad checksums, impossible lengths, unsupported header encodings, or trailing partial frames.
- Streaming provider responses no longer prebuffer the upstream iterator, leak it on disconnect/error, duplicate terminal events, expose exception details, or fabricate usage values.
- MCP import no longer exits the host process when the optional dependency is absent.
- CLI stop no longer targets a PID until persisted state and service ownership have been validated, and force stop now additionally proves process identity so a reused PID is never signalled.
- `ModelInfo.from_api()` no longer stores `None` in fields declared `str`, `int`, or `float` when upstream sends an explicit null, and no longer raises `AttributeError` for a null `tokenLimits`; an unusable `modelId` now raises `ValueError` instead of producing a mistyped model.
- `AssistantClient.list_models()` now validates the response envelope and reports malformed payloads as `APIError` rather than letting `KeyError` or `ValueError` escape the SDK.
- Importing `kirox.service.tray` no longer crashes on a host with no usable display. `pystray` probes a display backend while importing, so a headless Linux server raised `Xlib.error.DisplayNameError`, which the previous `except ImportError` guard did not catch. The tray now degrades to unavailable and reports a message that names the missing display as a possible cause.

## [1.0.0] - 2026-07-30

### Added

- Initial core API client with streaming and multi-model support.
- CLI with interactive chat and update checks.
- Background HTTP service and optional system tray.
- Token refresh scheduler and configuration management.
- OpenAI- and Anthropic-compatible bridge endpoints.
- MCP integration and unit, integration, end-to-end, and performance tests.

## [0.2.0] - 2026-07-30

### Added

- Background service module, system tray support, token scheduler, HTTP API server, and associated tests.

## [0.1.0] - 2026-07-30

### Added

- Initial project structure, API client, EventStream parser, CLI interface, and basic tests.

# Changelog

All notable changes to Kirox are documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.1] - 2026-08-27

### Fixed

- Every model Kirox lists is now callable. `list_models()` requested the upstream catalog with origin `AI_EDITOR`, which returns a superset that the runtime does not honor: eight of nineteen advertised models failed at request time with `INVALID_MODEL_ID`. Origin `IDE` returns exactly the set the runtime serves for a credential, so the advertised catalog and the usable catalog are the same list. The catalog now reflects an account's actual entitlement rather than the full product line-up, so it can be shorter than before and can differ between accounts.
- `AssistantClient.chat()` no longer discards the upstream error body. A streaming response has no body until it is read, so a non-200 status raised `APIError` carrying only a status code and the upstream `reason` was lost. `list_models()` keeps its error body too, matching `list_tools()`.
- An upstream HTTP 400 is now reported as HTTP 400 `invalid_request_error` instead of HTTP 502 `Upstream service request failed`. Upstream rejecting the request is the caller's error, not a gateway failure, and reporting it as 502 made a correctable mistake look like an outage. A known upstream `reason` code is translated into an actionable message — `INVALID_MODEL_ID` becomes `The requested model is not available for this account` — while an unrecognized code falls back to generic text. Upstream wording is still never forwarded.

## [1.2.0] - 2026-08-27

### Added

- Field-level configuration validation. `Config` now rejects a non-loopback `server_host`, an out-of-range or non-integer `server_port`, a non-positive `refresh_interval`, a non-boolean `auto_refresh`, an unknown `log_level`, and wrong-typed or empty string fields, raising `ConfigError` with the offending field name instead of failing later during service startup. A malformed or non-UTF-8 config file is reported as `ConfigError` too, so `except ConfigError` covers every load failure.
- `KIROX_CONFIG` selects the configuration file, and `KIROX_SERVER_HOST`, `KIROX_SERVER_PORT`, `KIROX_LOG_LEVEL`, and `KIROX_LOG_FILE` overlay individual fields. Every overlay is validated like a file value, and empty values overlay nothing.
- `AuthManager.profile_arn` public property, and an optional `source` argument so a credential's provenance is set at construction rather than patched afterwards.
- `/api/token/status` now reports the credential `source` label, making it possible to see which resolution tier produced the active credential without exposing any secret.
- `EventStreamMessage.body_object()` for callers that require a JSON object frame body.
- `kirox.utils.net.is_loopback_host()` as the single implementation of loopback classification, replacing three private copies in the server, state, and configuration layers.
- Tests for the credential database parsing paths, the service lifecycle (`run`, signal handling, startup rollback, dead-server detection, `main`), configuration validation and persistence, and loopback classification. Branch coverage rose from 84% to 89% and the enforced floor from 80% to 85%.

### Changed

- `Config.to_file()` writes UTF-8 atomically through a temporary file with owner-only permissions requested, so an interrupted write cannot truncate the configuration and a persisted `token` is not left group- or world-readable. Because the file is replaced rather than truncated, permissions set on it by hand do not survive a write, and a write interrupted by a process kill can leave a private temporary sibling that the next write removes. `Config.from_file()` reads UTF-8 explicitly instead of the platform locale encoding.
- Config string fields are stripped, so a padded `region` can no longer produce a malformed upstream URL. An empty or whitespace-only string in a config file is now a `ConfigError` where it was previously accepted and later treated as absent; environment overlays keep the older behavior of ignoring empty values.
- `log_level` is restricted to real Python level names. The aliases `WARN` and `FATAL`, which resolved through the previous `getattr(logging, ...)` lookup, are accepted and normalized to `WARNING` and `CRITICAL` rather than rejected.
- The HTTP application resolves its own lazily created credentials through `AuthManager.resolve(config=...)` instead of branching on `config.token`. The documented precedence, including the rule that a config token duplicating `KIROX_TOKEN` is environment provenance, now applies to that path, so the reported `source` cannot misattribute an environment credential as a config credential. A configured environment token is now preferred over a configured database for this path, matching the documented order.
- A malformed EventStream frame body is now terminal for `AssistantClient.chat()`. Non-`assistantResponseEvent` frames are still forwarded as `StreamEvent.raw`, but only when their body decodes to a JSON object; a non-object body ends the stream instead of being passed through as a value the field is not declared to hold, and content already yielded before the bad frame is not retained by `chat_simple()`.
- The default configuration path is resolved per call again rather than at import, so a process that changes its home directory before loading sees the new location. `default_config_path()` and `get_config_path()` expose that resolution.
- A bare `kirox` invocation now takes its defaults from the `run` subparser through `set_defaults`, so adding a `run` flag can no longer leave the implicit form missing an attribute.
- The CLI update-check cache is read and written as UTF-8.
- `AssistantClient.list_models()` now releases its response on every path, matching `list_tools()`. This is consistency rather than a leak fix: a non-streaming `httpx` response has already been read.

### Fixed

- A credential secret named as a refresh token is no longer selected as the bearer token when its value carries the access-token prefix. The previous condition let the value heuristic override the refresh-name exclusion, so an `aoa`-prefixed refresh secret could be sent as the `Authorization` token and fail every upstream call with HTTP 401, which the scheduler would then retry against the same wrong secret. A name that also says `access`, such as a refreshable session's access token, stays eligible.
- Malformed upstream EventStream bodies now raise `StreamError` instead of escaping as `json.JSONDecodeError`, so the HTTP bridge reports upstream corruption as HTTP 502 rather than HTTP 500, and a non-object frame body or a non-string assistant `content` is rejected instead of being stored in fields declared otherwise. A non-string `modelId` is dropped rather than stored.
- The HTTP application now closes a client it created lazily, and that teardown is terminal: a later request fails instead of silently building a replacement client. `run_server()` previously leaked the `httpx.Client` built on first request; an injected client is still owned and closed by the managed service.
- `AuthManager._profile_arn` is no longer read from outside the class by the client and HTTP layers.
- The update check compares release segments instead of testing string inequality, so an index version behind the installed build is no longer announced as an available update. Running `kirox status` on a build newer than PyPI previously printed the older index version under "Update available".

### Known limitations

- On Windows `os.chmod` cannot express owner-only access, so a config file holding a `token` inherits directory ACLs. The atomic replace and the temporary-file sweep still apply.
- `run_server()` and the managed service both stop the HTTP server before closing the client, and Werkzeug's threaded server does not join request threads. An SSE response still writing at that moment observes a closed client and terminates with a sanitized provider error event rather than a truncated stream.

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

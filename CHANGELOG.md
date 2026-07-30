# Changelog

All notable changes to kirox will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-07-30

### Added
- Initial release
- Core API client with streaming support
- Multi-model support (Claude, GPT, Deepseek, etc.)
- CLI with interactive chat
- Background service with HTTP API
- System tray icon (Windows)
- Token auto-refresh scheduler
- MCP server for AI assistants
- Configuration management
- Comprehensive test suite (33 tests)
- Auto-update checking
- REST API endpoints

### Features
- **Core SDK**: Full API client with streaming
- **CLI**: `kirox` command with subcommands
- **Service**: Background HTTP server
- **Tray**: Windows system tray icon
- **Scheduler**: Automatic token refresh
- **Config**: JSON config + environment variables
- **Tests**: Unit, integration, E2E, performance
- **Docs**: Installation, configuration, API reference

## [0.2.0] - 2026-07-30

### Added
- Background service module
- System tray icon support
- Token scheduler
- HTTP API server
- Unit tests for config and logging
- Integration tests for server and scheduler

## [0.1.0] - 2026-07-30

### Added
- Initial project structure
- Core API client
- EventStream parser
- CLI interface
- Basic tests

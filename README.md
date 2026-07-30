<div align="center">

# ⚡ Kirox

### Production-Ready SDK for AI Coding Assistants

[![PyPI version](https://badge.fury.io/py/kirox.svg)](https://pypi.org/project/kirox/)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-33%20passing-brightgreen.svg)](#testing)

---

**One command to access Claude, GPT, Deepseek, and more.**

Background service • System tray • Auto-refresh tokens • REST API • MCP Server

</div>

---

## 🚀 Quick Start

```bash
# Install globally
pip install kirox

# Start everything (service + tray + auto-refresh)
kirox
```

That's it. Kirox handles everything automatically.

---

## 📦 Installation

### From PyPI (Recommended)

```bash
pip install kirox
```

### With Background Service

```bash
pip install kirox[service]
```

### From Source

```bash
git clone https://github.com/idugeni/kirox.git
cd kirox
pip install -e ".[dev,service]"
```

---

## 🎯 Commands

| Command | Description |
|---------|-------------|
| `kirox` | Start everything (service + tray) |
| `kirox status` | Check service status |
| `kirox stop` | Stop the service |
| `kirox update` | Update to latest version |
| `kirox models` | List available models |
| `kirox chat` | Interactive chat |
| `kirox ask "Hello"` | One-shot question |

### Options

```bash
kirox --no-tray       # Run without tray icon
kirox --no-update     # Skip update check
kirox -v              # Verbose output
kirox chat -m gpt-5.6-sol  # Use specific model
```

---

## 🐍 Python API

### Basic Usage

```python
from kirox import AssistantClient

# Auto-detect credentials from kiro-cli
client = AssistantClient.from_cli_db()

# Streaming chat
for event in client.chat("Explain quantum computing"):
    if event.content:
        print(event.content, end="")

# One-shot
response = client.chat_simple("What is 2+2?")
print(response)
```

### Available Models

```python
client = AssistantClient.from_cli_db()

for model in client.list_models():
    print(f"{model.model_id}: {model.model_name} ({model.rate_multiplier}x)")
```

### Custom Auth

```python
from kirox import AssistantClient
from kirox.core.auth import AuthManager

auth = AuthManager(token="Bearer ...", profile_arn="arn:...")
client = AssistantClient(auth=auth)
```

---

## ⚙️ Configuration

### Config File

Location: `~/.kuro/config.json`

```json
{
  "region": "us-east-1",
  "server_port": 8420,
  "server_host": "127.0.0.1",
  "auto_refresh": true,
  "refresh_interval": 3000,
  "log_level": "INFO"
}
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `KURO_TOKEN` | Bearer token |
| `KURO_PROFILE_ARN` | AWS profile ARN |
| `KURO_REGION` | AWS region |

---

## 🔌 REST API

When running, kirox exposes a local HTTP API:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/models` | GET | List available models |
| `/chat` | POST | Send chat message |
| `/token/status` | GET | Check token status |

### Example

```bash
# Health check
curl http://localhost:8420/health

# List models
curl http://localhost:8420/models

# Chat
curl -X POST http://localhost:8420/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello!", "model": "auto"}'
```

---

## 🤖 MCP Server

Connect kirox with AI assistants like Claude Code or MiMo:

```json
{
  "mcpServers": {
    "kirox": {
      "command": "kirox-mcp",
      "env": {
        "KURO_TOKEN": "Bearer ..."
      }
    }
  }
}
```

---

## 🧪 Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=kirox

# Run specific test suite
pytest tests/unit/
pytest tests/integration/
pytest tests/e2e/
pytest tests/performance/
```

### Test Coverage

- **Unit Tests**: Config, logging, eventstream parser
- **Integration Tests**: HTTP server, token scheduler
- **E2E Tests**: Mock server, full API flow
- **Performance Tests**: Parser speed, concurrent operations

---

## 🏗️ Architecture

```
kirox/
├── src/kirox/
│   ├── core/           # API client, auth, eventstream parser
│   ├── cli/            # Command-line interface
│   ├── mcp/            # MCP server for AI assistants
│   ├── service/        # Background service, tray, scheduler
│   └── utils/          # Configuration, logging
├── tests/
│   ├── unit/           # Unit tests
│   ├── integration/    # Integration tests
│   ├── e2e/            # End-to-end tests
│   └── performance/    # Performance benchmarks
└── docs/               # Documentation
```

---

## 🔄 Auto-Update

Kirox automatically checks for updates when started. To update manually:

```bash
kirox update          # Interactive update
kirox update -y       # Update without confirmation
```

---

## 📚 Documentation

- [Installation Guide](docs/installation.md)
- [Configuration](docs/configuration.md)
- [API Reference](docs/api-reference.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

---

## 🤝 Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

<div align="center">

**Made with ❤️ by the Kirox Community**

[Report Bug](https://github.com/idugeni/kirox/issues) • [Request Feature](https://github.com/idugeni/kirox/issues) • [Documentation](https://github.com/idugeni/kirox#readme)

</div>

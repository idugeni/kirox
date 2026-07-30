<div align="center">

# ⚡ Kirox

### Production-Ready SDK for AI Coding Assistants

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-31%20passing-brightgreen.svg)](#testing)

---

**One command to access Claude, GPT, Deepseek, and more.**

Background service • System tray • OpenAI & Anthropic compatible API • MCP Server

</div>

---

## 🚀 Quick Start

```bash
# Install globally
pip install kirox

# Start everything (service + tray + auto-refresh)
kirox
```

---

## 📦 Installation

```bash
pip install kirox           # Basic
pip install kirox[service]  # With tray icon
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

---

## 🌉 API Bridge

Kirox runs as a **local API bridge** that exposes OpenAI & Anthropic compatible endpoints.

### Base URL

```
http://localhost:8420
```

### OpenAI Compatible

```bash
# List models
curl http://localhost:8420/v1/models

# Chat completion
curl http://localhost:8420/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'

# Streaming
curl http://localhost:8420/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": true
  }'
```

### Anthropic Compatible

```bash
# Messages API
curl http://localhost:8420/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello!"}]
  }'

# Streaming
curl http://localhost:8420/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": true
  }'
```

### Use with CLI Tools

```bash
# OpenAI CLI
export OPENAI_API_KEY=any
export OPENAI_BASE_URL=http://localhost:8420/v1
openai api chat.completions.create -m auto -g user -k "Hello!"

# curl one-liner
curl -s http://localhost:8420/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"Hi"}]}' | jq
```

### Use with IDEs

Configure your IDE to use `http://localhost:8420/v1` as the API endpoint.

---

## 🐍 Python API

```python
from kirox import AssistantClient

client = AssistantClient.from_cli_db()

# Streaming
for event in client.chat("Hello"):
    if event.content:
        print(event.content, end="")

# One-shot
print(client.chat_simple("What is 2+2?"))
```

---

## ⚙️ Configuration

```json
{
  "region": "us-east-1",
  "server_port": 8420,
  "auto_refresh": true,
  "refresh_interval": 3000
}
```

Location: `~/.kuro/config.json`

---

## 🧪 Testing

```bash
pytest tests/ -v
```

### Test Coverage

- **Unit Tests**: Config, logging, eventstream parser
- **Integration Tests**: HTTP server (OpenAI + Anthropic endpoints)
- **E2E Tests**: Mock server, full API flow
- **Performance Tests**: Parser speed, concurrent operations

---

## 📚 Documentation

- [Installation Guide](docs/installation.md)
- [Configuration](docs/configuration.md)
- [API Reference](docs/api-reference.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

---

## 📄 License

MIT License - see [LICENSE](LICENSE)

---

<div align="center">

**Made with ❤️ by the Kirox Community**

</div>

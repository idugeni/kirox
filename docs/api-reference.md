# API Reference

## REST Endpoints

When running the background service, kirox exposes a local HTTP API.

### Health Check

```
GET /health
```

**Response:**
```json
{
  "status": "ok",
  "version": "1.0.0"
}
```

### List Models

```
GET /models
```

**Response:**
```json
{
  "models": [
    {
      "id": "auto",
      "name": "Auto",
      "rate": 1.0,
      "thinking": false
    },
    {
      "id": "claude-opus-5",
      "name": "Claude Opus 5",
      "rate": 2.2,
      "thinking": true
    }
  ]
}
```

### Chat

```
POST /chat
Content-Type: application/json
```

**Request:**
```json
{
  "message": "Hello, what can you do?",
  "model": "auto"
}
```

**Response:**
```json
{
  "response": "I can help you with coding tasks..."
}
```

### Token Status

```
GET /token/status
```

**Response:**
```json
{
  "authenticated": true,
  "has_profile": true
}
```

---

## Python API

### AssistantClient

The main client for interacting with AI coding assistants.

```python
from kirox import AssistantClient

# Auto-detect credentials
client = AssistantClient.from_cli_db()

# Or with explicit auth
from kirox.core.auth import AuthManager
auth = AuthManager(token="Bearer ...", profile_arn="arn:...")
client = AssistantClient(auth=auth)
```

#### Methods

##### `list_models()`

Fetch available models.

```python
models = client.list_models()
for m in models:
    print(f"{m.model_id}: {m.model_name}")
```

##### `chat(message, model_id="auto")`

Send a chat message with streaming response.

```python
for event in client.chat("Hello"):
    if event.content:
        print(event.content, end="")
```

##### `chat_simple(message, model_id="auto")`

Send a chat message and return the full response.

```python
response = client.chat_simple("What is 2+2?")
print(response)
```

##### `list_tools()`

Fetch available tools.

```python
tools = client.list_tools()
for t in tools:
    print(f"{t.name}: {t.description}")
```

---

### AuthManager

Manages authentication tokens.

```python
from kirox.core.auth import AuthManager

# From environment
auth = AuthManager.from_env()

# From CLI database
auth = AuthManager.from_cli_db()

# Direct token
auth = AuthManager(token="Bearer ...", profile_arn="arn:...")
```

---

### EventStream Parser

Parse Amazon EventStream binary protocol.

```python
from kirox import parse_eventstream

for message in parse_eventstream(binary_data):
    print(f"Event: {message.event_type}")
    print(f"Body: {message.body_json()}")
```

---

### Configuration

```python
from kirox.utils.config import Config, load_config

# Load config
config = load_config()

# Create config
config = Config(region="us-east-1", server_port=8420)
```

---

## Data Models

### ModelInfo

```python
@dataclass
class ModelInfo:
    model_id: str
    model_name: str
    description: str
    rate_multiplier: float
    rate_unit: str
    token_limits: TokenLimits
    supports_thinking: bool
```

### ToolSpec

```python
@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: tuple[ToolParam, ...]
```

### StreamEvent

```python
@dataclass
class StreamEvent:
    event_type: str
    content: str | None
    model_id: str | None
    done: bool
    raw: dict | None
```

---

## Error Handling

```python
from kirox.core.errors import (
    KuroError,          # Base exception
    AuthenticationError, # Auth failures
    APIError,           # API request failures
    StreamError,        # Stream parsing failures
)

try:
    client.chat("Hello")
except AuthenticationError:
    print("Not authenticated")
except APIError as e:
    print(f"API error: {e.status}")
except StreamError:
    print("Failed to parse response")
```

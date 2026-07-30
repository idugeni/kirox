# API Reference

## Base URL

```
http://localhost:8420
```

## Endpoints

### OpenAI Compatible

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/models` | GET | List available models |
| `/v1/chat/completions` | POST | Chat completion |

### Anthropic Compatible

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/messages` | POST | Messages API |

### Kirox Native

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/` | GET | Service info |
| `/api/models` | GET | List models |
| `/api/chat` | POST | Chat |
| `/api/token/status` | GET | Token status |

---

## OpenAI Compatible API

### List Models

```
GET /v1/models
```

Response:
```json
{
  "object": "list",
  "data": [
    {
      "id": "auto",
      "object": "model",
      "created": 1234567890,
      "owned_by": "kirox"
    }
  ]
}
```

### Chat Completions

```
POST /v1/chat/completions
```

Request:
```json
{
  "model": "auto",
  "messages": [
    {"role": "user", "content": "Hello!"}
  ],
  "stream": false
}
```

Response:
```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "auto",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "Hi there!"},
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}
```

---

## Anthropic Compatible API

### Messages

```
POST /v1/messages
```

Request:
```json
{
  "model": "auto",
  "max_tokens": 1024,
  "messages": [
    {"role": "user", "content": "Hello!"}
  ],
  "stream": false
}
```

Response:
```json
{
  "id": "msg_abc123",
  "type": "message",
  "role": "assistant",
  "content": [{"type": "text", "text": "Hi there!"}],
  "model": "auto",
  "stop_reason": "end_turn",
  "usage": {"input_tokens": 0, "output_tokens": 0}
}
```

---

## Streaming

Both OpenAI and Anthropic endpoints support streaming.

### OpenAI Stream Format

```
data: {"choices": [{"delta": {"content": "Hello"}}]}

data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}

data: [DONE]
```

### Anthropic Stream Format

```
event: message_start
data: {"type": "message_start", "message": {...}}

event: content_block_start
data: {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}

event: content_block_delta
data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}}

event: content_block_stop
data: {"type": "content_block_stop", "index": 0}

event: message_delta
data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}

event: message_stop
data: {"type": "message_stop"}
```

---

## Python API

```python
from kirox import AssistantClient

client = AssistantClient.from_cli_db()

# List models
models = client.list_models()

# Chat
response = client.chat_simple("Hello!")

# Streaming
for event in client.chat("Hello"):
    if event.content:
        print(event.content, end="")
```

---

## Error Handling

```python
from kirox.core.errors import AuthenticationError, APIError

try:
    client.chat_simple("Hello")
except AuthenticationError:
    print("Not authenticated")
except APIError as e:
    print(f"API error: {e.status}")
```

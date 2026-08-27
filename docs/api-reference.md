# API Reference

## Local base URL

The managed bridge defaults to:

```text
http://127.0.0.1:8420
```

Only loopback hosts are accepted. The configured port may differ, and port `0` selects an ephemeral port for embedded/test use.

## Python API

```python
from kirox import AssistantClient

with AssistantClient.auto() as client:
    models = client.list_models()
    answer = client.chat_simple("Hello", model_id="auto")
    for event in client.chat("Stream this", model_id="auto"):
        if event.content:
            print(event.content, end="")
```

### Capability boundaries

- **Direct Python SDK:** `AssistantClient` communicates directly with upstream services. `list_tools()` returns `ToolSpec` objects; each spec preserves the complete JSON Schema in `input_schema`, exposes `parameters` as a convenient view, and can be serialized without schema loss using `to_api()`. The response envelope is validated strictly: a missing `result`, a missing or non-array `tools` list, a JSON-RPC `error` member, or an entry without a non-empty `name` and an `inputSchema` object raises `APIError` instead of yielding partially formed specs. `chat(..., tools=...)` accepts either `ToolSpec` objects or raw mappings. Non-assistant upstream events are available in `StreamEvent.raw`; the SDK does not execute tools or automatically send tool results.
- **Local HTTP bridge:** The native, OpenAI-compatible, and Anthropic-compatible routes remain text-only. They reject tools, tool calls, and tool results rather than forwarding or silently dropping them.
- **Local MCP server:** MCP exposes only the text-only `kirox_chat` wrapper. Its tool listing describes that local wrapper; it is not a proxy for upstream tool discovery.

`chat()` returns `StreamEvent` objects as validated AWS EventStream frames arrive. It verifies prelude CRC, message CRC, total/header lengths, header encodings, and truncated final frames. Close the generator when stopping early; a context-managed `AssistantClient` closes its owned `httpx.Client`.

## Route summary

| Route | Method | Contract |
|---|---|---|
| `/health` | GET | Status, package version, and bridge name |
| `/` | GET | Service metadata and route families |
| `/api/models` | GET | Native compact model list |
| `/api/chat` | POST | Native text request; model defaults to `auto` |
| `/api/token/status` | GET | Authentication/profile booleans and the credential source label |
| `/v1/models` | GET | OpenAI-style model list |
| `/v1/chat/completions` | POST | OpenAI-style text chat, buffered or SSE |
| `/v1/messages` | POST | Anthropic-style text messages, buffered or SSE |

The internal shutdown route is not a public API. It requires loopback, a per-process control token, and managed service ownership.

### Token status

```http
GET /api/token/status
```

```json
{"authenticated": true, "has_profile": true, "source": "cli-db:fixed"}
```

`source` names the resolution tier that produced the active credential — `explicit`, `config`, `environment:KIROX`, `environment:ASSISTANT`, `cli-db:configured`, `cli-db:fixed`, or `unknown`. It is a provenance label for debugging which credential Kirox picked; no token or profile value is returned.

## Shared text-only contract

Provider-compatible requests are validated rather than loosely coerced.

- Body must be a JSON object.
- `model` is required and must be a non-empty string.
- `messages` is required and must be a non-empty array.
- Roles are limited to `system`, `user`, and `assistant`; the final message must be `user`.
- Content must be non-whitespace text or a non-empty array of `{ "type": "text", "text": "..." }` blocks.
- `stream`, if present, must be a boolean.
- `max_tokens`, if present, must be a positive integer. It is accepted but not enforced, so Kirox returns HTTP `Warning: 299`.
- OpenAI top-level fields are limited to `model`, `messages`, `stream`, and `max_tokens`.
- Anthropic also accepts top-level `system` text/text blocks.

Unsupported fields and semantics return field-addressed HTTP 400 errors. This includes tools, tool choice, tool calls/results, images, audio, metadata blocks, `temperature`, `top_p`, and unknown nested fields. Kirox never silently drops them.

History is serialized to a canonical upstream transcript:

```text
SYSTEM:
System instruction

USER:
Question

ASSISTANT:
Earlier answer

USER:
Follow-up
```

## OpenAI-compatible API

### Models

```http
GET /v1/models
```

```json
{
  "object": "list",
  "data": [
    {"id": "auto", "object": "model", "created": 1234567890, "owned_by": "kirox"}
  ]
}
```

The catalog is requested from the upstream management plane with origin `AI_EDITOR`, the full catalog, and the runtime serves all of it.

Kirox calls the CodeWhisperer streaming endpoint, `https://codewhisperer.{region}.amazonaws.com`, with the `AmazonCodeWhispererStreamingService` target prefix. The Kiro-branded runtime `runtime.{region}.kiro.dev` accepts the same request shape and returns the same EventStream frames, but rejects the newest models with `INVALID_MODEL_ID` regardless of catalog origin, request headers, or request fields. Kirox uses the endpoint that can run the catalog it lists. Pass `runtime_url` to `AssistantClient` to override the default.

### Chat completion

```http
POST /v1/chat/completions
Content-Type: application/json
```

```json
{
  "model": "auto",
  "messages": [{"role": "user", "content": "Hello"}],
  "stream": false
}
```

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "auto",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "Hello"},
      "finish_reason": "stop"
    }
  ]
}
```

No `usage` object is emitted because the upstream text bridge does not provide reliable token accounting.

### OpenAI SSE

Each content event is a `chat.completion.chunk` with a stable ID and timestamp for the stream. A normal stream emits one stop chunk and exactly one:

```text
data: [DONE]
```

A runtime failure becomes a sanitized error data event followed by the same single `[DONE]`. Client disconnect closes the upstream generator without prebuffering remaining chunks.

## Anthropic-compatible API

```http
POST /v1/messages
Content-Type: application/json
```

```json
{
  "model": "auto",
  "max_tokens": 1024,
  "system": "Be concise",
  "messages": [{"role": "user", "content": "Hello"}],
  "stream": false
}
```

```json
{
  "id": "msg_...",
  "type": "message",
  "role": "assistant",
  "content": [{"type": "text", "text": "Hello"}],
  "model": "auto",
  "stop_reason": "end_turn",
  "stop_sequence": null
}
```

No fabricated `usage` field is emitted.

### Anthropic SSE

A normal stream uses this order:

1. `message_start`
2. `content_block_start`
3. zero or more `content_block_delta`
4. `content_block_stop`
5. `message_delta`
6. `message_stop`

A failure emits one sanitized terminal `error` event and does not also emit `message_stop`.

## Request preconditions

Every request is checked before routing:

- The `Host` header must resolve to a loopback name or address (`localhost`, `127.0.0.1`, `::1`, or an IPv4-mapped loopback), with an optional valid port. Any other, malformed, or missing `Host` value returns HTTP 400 `Invalid Host header`, which blocks DNS-rebinding access from a browser page.
- Request bodies are limited to 1 MiB. A larger body returns HTTP 413 before validation or upstream work, using the provider envelope of the requested route (`param: "body"`, `code: "request_too_large"`) and a plain `error` message on native routes.

## Errors

Validation errors are HTTP 400 with provider-appropriate envelopes and fields including `type`, `message`, `param`, and `code`. Runtime errors are sanitized:

- Authentication failures: HTTP 401, `Authentication required`
- Upstream request rejections (HTTP 400): HTTP 400, `invalid_request_error`. A known upstream `reason` code is translated into an actionable message — `INVALID_MODEL_ID` becomes `The requested model is not available for this account` — and anything unrecognized falls back to `Upstream rejected the request as invalid`. Upstream wording is never forwarded.
- Upstream API/EventStream/httpx failures: HTTP 502, `Upstream service request failed`
- Unexpected internal failures: HTTP 500, `Internal server error`
- Oversized bodies: HTTP 413, `Request body exceeds 1 MiB limit`
- Non-loopback or malformed `Host`: HTTP 400, `Invalid Host header`

Exception messages, response bodies, credentials, and payload details are not returned to downstream clients.

## MCP tool

Install `kirox[mcp]` and launch `kirox-mcp` over stdio. The server advertises:

```json
{
  "name": "kirox_chat",
  "inputSchema": {
    "type": "object",
    "properties": {
      "message": {"type": "string", "minLength": 1},
      "model": {"type": "string", "minLength": 1, "default": "auto"}
    },
    "required": ["message"],
    "additionalProperties": false
  }
}
```

Only text output is returned. Unknown tools/arguments and empty or incorrectly typed values are errors. The synchronous Kirox request runs through `asyncio.to_thread`, and the single owned client closes at the end of the MCP lifespan.

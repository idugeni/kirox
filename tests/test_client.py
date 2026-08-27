"""Tests for incremental client streaming and resource ownership."""

import json
import struct
import zlib
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from kirox.core.auth import AuthManager
from kirox.core.client import (
    CATALOG_ORIGIN,
    RUNTIME_SERVICE,
    AssistantClient,
    _default_runtime_url,
)
from kirox.core.errors import APIError, StreamError
from kirox.core.eventstream import EventStreamDecoder
from kirox.core.models import ToolSpec


def event_message(event_type: str, body_data: dict[str, Any]) -> bytes:
    return raw_event_message(event_type, json.dumps(body_data).encode())


def raw_event_message(event_type: str, body: bytes) -> bytes:
    name = b":event-type"
    value = event_type.encode()
    headers = bytes([len(name)]) + name + b"\x07" + struct.pack(">H", len(value)) + value
    total_length = 16 + len(headers) + len(body)
    prelude = struct.pack(">II", total_length, len(headers))
    prelude += struct.pack(">I", zlib.crc32(prelude) & 0xFFFFFFFF)
    message = prelude + headers + body
    return message + struct.pack(">I", zlib.crc32(message) & 0xFFFFFFFF)


def assistant_message(content: str | None, model_id: str = "model") -> bytes:
    return event_message("assistantResponseEvent", {"content": content, "modelId": model_id})


class TrackingStream(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.yield_count = 0
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self.chunks:
            self.yield_count += 1
            yield chunk

    def close(self) -> None:
        self.closed = True


def test_chat_consumes_incrementally_and_closes_when_cancelled() -> None:
    stream = TrackingStream([assistant_message("first"), assistant_message("second")])
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    )
    client = AssistantClient(
        AuthManager(token="token"),
        "https://runtime.test",
        "us-east-1",
        http_client,
    )
    generator = client.chat("hello")

    assert next(generator).content == "first"
    assert stream.yield_count == 1
    assert not stream.closed

    generator.close()
    assert stream.closed
    client.close()


def test_chat_propagates_stream_error_and_closes_response() -> None:
    corrupted = bytearray(assistant_message("broken"))
    corrupted[-1] ^= 0x01
    stream = TrackingStream([bytes(corrupted)])
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    )
    client = AssistantClient(
        auth=AuthManager(token="token"),
        runtime_url="https://runtime.test",
        http_client=http_client,
    )

    with pytest.raises(StreamError, match="message CRC"):
        list(client.chat("hello"))

    assert stream.closed
    client.close()


def test_error_response_is_closed() -> None:
    stream = TrackingStream([b"error"])
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(500, stream=stream))
    )
    client = AssistantClient(
        auth=AuthManager(token="token"),
        runtime_url="https://runtime.test",
        http_client=http_client,
    )

    with pytest.raises(APIError):
        list(client.chat("hello"))

    assert stream.closed
    client.close()


def test_chat_simple_uses_unique_conversation_ids_and_replaced_auth() -> None:
    requests: list[httpx.Request] = []
    streams: list[TrackingStream] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        stream = TrackingStream([assistant_message("answer"), assistant_message(None)])
        streams.append(stream)
        return httpx.Response(200, stream=stream)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = AssistantClient(
        auth=AuthManager(token="old-token"),
        runtime_url="https://runtime.test",
        http_client=http_client,
    )
    client.replace_auth(AuthManager(token="new-token"))

    assert client.chat_simple("one") == "answer"
    assert client.chat_simple("two", "model-two") == "answer"

    bodies = [json.loads(request.content) for request in requests]
    conversation_ids = [body["conversationState"]["conversationId"] for body in bodies]
    assert len(set(conversation_ids)) == 2
    assert all(value.startswith("conv_") for value in conversation_ids)
    assert requests[0].headers["Authorization"] == "Bearer new-token"
    assert (
        bodies[1]["conversationState"]["currentMessage"]["userInputMessage"]["modelId"]
        == "model-two"
    )
    assert all(stream.closed for stream in streams)
    client.close()


def test_factories_and_public_exports_remain_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = AuthManager(token="token")
    monkeypatch.setattr(AuthManager, "from_env", classmethod(lambda cls: auth))
    http_client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))

    client = AssistantClient.from_env("eu-west-1", http_client=http_client)

    assert client.auth is auth
    assert client._client is http_client
    assert EventStreamDecoder is not None
    from kirox import EventStreamDecoder as PublicDecoder

    assert PublicDecoder is EventStreamDecoder
    client.close()


def test_list_tools_preserves_request_parses_schema_and_closes_response() -> None:
    requests: list[httpx.Request] = []
    stream = TrackingStream(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "tools_list",
                    "result": {
                        "tools": [
                            {
                                "name": "search",
                                "description": "Search",
                                "inputSchema": {
                                    "json": {
                                        "type": "object",
                                        "properties": {"q": {"type": "string", "enum": ["a", "b"]}},
                                        "required": ["q"],
                                        "additionalProperties": False,
                                    }
                                },
                            }
                        ]
                    },
                }
            ).encode()
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, stream=stream)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = AssistantClient(
        auth=AuthManager(token="token", profile_arn="arn:test"),
        runtime_url="https://runtime.test",
        http_client=http_client,
    )

    tools = client.list_tools()

    assert len(tools) == 1
    assert tools[0].name == "search"
    assert tools[0].input_schema["additionalProperties"] is False
    assert tools[0].input_schema["properties"]["q"]["enum"] == ["a", "b"]
    assert requests[0].url == "https://runtime.test"
    assert requests[0].headers["x-amz-target"] == f"{RUNTIME_SERVICE}.InvokeMCP"
    assert json.loads(requests[0].content) == {
        "id": "tools_list",
        "method": "tools/list",
        "profileArn": "arn:test",
        "jsonrpc": "2.0",
        "params": {"includeHidden": True},
    }
    assert stream.closed
    client.close()


def test_list_tools_http_error_has_status_body_and_closes_response() -> None:
    stream = TrackingStream([b"upstream failure"])
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(503, stream=stream))
    )
    client = AssistantClient(
        auth=AuthManager(token="token"),
        runtime_url="https://runtime.test",
        http_client=http_client,
    )

    with pytest.raises(APIError) as exc_info:
        client.list_tools()

    assert exc_info.value.status == 503
    assert exc_info.value.response_body == "upstream failure"
    assert stream.closed
    client.close()


def test_list_tools_json_rpc_error_keeps_response_body_and_closes() -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": "tools_list",
        "error": {"code": -32603, "message": "tool registry unavailable"},
    }
    stream = TrackingStream([json.dumps(payload).encode()])
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    )
    client = AssistantClient(
        auth=AuthManager(token="token"),
        runtime_url="https://runtime.test",
        http_client=http_client,
    )

    with pytest.raises(APIError) as exc_info:
        client.list_tools()

    assert exc_info.value.status == 200
    assert exc_info.value.response_body == payload
    assert stream.closed
    client.close()


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        json.dumps({"jsonrpc": "2.0", "result": None}).encode(),
        json.dumps({"jsonrpc": "2.0", "result": {}}).encode(),
        json.dumps({"jsonrpc": "2.0", "result": {"tools": {}}}).encode(),
        json.dumps({"jsonrpc": "2.0", "result": {"tools": [{}]}}).encode(),
        json.dumps({"jsonrpc": "2.0", "result": {"tools": [{"name": "missing-schema"}]}}).encode(),
    ],
)
def test_list_tools_invalid_response_is_api_error_and_closes(body: bytes) -> None:
    stream = TrackingStream([body])
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    )
    client = AssistantClient(
        auth=AuthManager(token="token"),
        runtime_url="https://runtime.test",
        http_client=http_client,
    )

    with pytest.raises(APIError):
        client.list_tools()

    assert stream.closed
    client.close()


def test_list_tools_allows_empty_success_and_closes_response() -> None:
    stream = TrackingStream([json.dumps({"result": {"tools": []}}).encode()])
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    )
    client = AssistantClient(
        auth=AuthManager(token="token"),
        runtime_url="https://runtime.test",
        http_client=http_client,
    )

    assert client.list_tools() == []
    assert stream.closed
    client.close()


def test_list_models_requests_the_runtime_entitled_catalog() -> None:
    seen: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"models": []})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = AssistantClient(
        auth=AuthManager(token="token", profile_arn="arn"),
        http_client=http_client,
    )

    client.list_models()

    # The runtime serves the full catalog, so Kirox asks for the full catalog.
    assert seen == [{"origin": "AI_EDITOR", "profileArn": "arn"}]
    assert CATALOG_ORIGIN == "AI_EDITOR"
    client.close()


def test_runtime_defaults_to_the_endpoint_that_serves_every_model() -> None:
    # runtime.{region}.kiro.dev rejects the newest models with INVALID_MODEL_ID
    # regardless of origin, header, or request field. The CodeWhisperer streaming
    # endpoint accepts the same request shape and serves the whole catalog.
    assert _default_runtime_url("us-east-1") == "https://codewhisperer.us-east-1.amazonaws.com"
    assert (
        _default_runtime_url("eu-central-1") == "https://codewhisperer.eu-central-1.amazonaws.com"
    )
    assert RUNTIME_SERVICE == "AmazonCodeWhispererStreamingService"


def test_chat_targets_the_runtime_service_and_honors_an_injected_url() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = AssistantClient(
        auth=AuthManager(token="token"),
        runtime_url="https://runtime.test",
        http_client=http_client,
    )

    list(client.chat("hello"))

    assert requests[0].url == "https://runtime.test"
    assert requests[0].headers["x-amz-target"] == f"{RUNTIME_SERVICE}.GenerateAssistantResponse"
    client.close()


def test_list_models_error_keeps_the_upstream_body() -> None:
    body = {"__type": "ValidationException", "reason": "INVALID_PROFILE"}
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(400, json=body))
    )
    client = AssistantClient(auth=AuthManager(token="token"), http_client=http_client)

    with pytest.raises(APIError) as exc_info:
        client.list_models()

    assert exc_info.value.status == 400
    assert exc_info.value.response_body == body
    client.close()


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (json.dumps({"reason": "INVALID_MODEL_ID"}).encode(), {"reason": "INVALID_MODEL_ID"}),
        (b"not json at all", "not json at all"),
    ],
)
def test_chat_error_keeps_the_upstream_body(content: bytes, expected: Any) -> None:
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(400, content=content))
    )
    client = AssistantClient(auth=AuthManager(token="token"), http_client=http_client)

    with pytest.raises(APIError) as exc_info:
        list(client.chat("hello", model_id="claude-sonnet-5"))

    assert exc_info.value.status == 400
    assert exc_info.value.response_body == expected
    client.close()


def test_list_models_parses_payload_and_replaces_null_fields() -> None:
    payload = {
        "models": [
            {"modelId": "a", "modelName": None, "tokenLimits": {"maxInputTokens": 10}},
            {"modelId": "b", "modelName": "B", "rateMultiplier": 2},
        ]
    }
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )
    client = AssistantClient(auth=AuthManager(token="token"), http_client=http_client)

    models = client.list_models()

    assert [model.model_id for model in models] == ["a", "b"]
    assert models[0].model_name == "a"
    assert models[0].token_limits.max_input_tokens == 10
    assert models[0].token_limits.max_output_tokens == 64000
    assert models[1].rate_multiplier == 2.0
    client.close()


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        json.dumps([]).encode(),
        json.dumps({"models": {}}).encode(),
        json.dumps({"models": ["a"]}).encode(),
        json.dumps({"models": [{"modelName": "no-id"}]}).encode(),
    ],
)
def test_list_models_invalid_response_is_api_error(body: bytes) -> None:
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    )
    client = AssistantClient(auth=AuthManager(token="token"), http_client=http_client)

    with pytest.raises(APIError) as exc_info:
        client.list_models()

    assert exc_info.value.status == 200
    client.close()


def test_list_models_allows_missing_models_member() -> None:
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    )
    client = AssistantClient(auth=AuthManager(token="token"), http_client=http_client)

    assert client.list_models() == []
    client.close()


def test_list_models_http_error_carries_status() -> None:
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(503, json={}))
    )
    client = AssistantClient(auth=AuthManager(token="token"), http_client=http_client)

    with pytest.raises(APIError) as exc_info:
        client.list_models()

    assert exc_info.value.status == 503
    client.close()


def test_list_tools_output_round_trips_through_chat_losslessly() -> None:
    schema = {
        "type": "object",
        "properties": {"q": {"type": "string", "enum": ["a", "b"]}},
        "additionalProperties": False,
    }
    list_stream = TrackingStream(
        [
            json.dumps(
                {
                    "result": {
                        "tools": [
                            {
                                "name": "search",
                                "description": "Search",
                                "inputSchema": {"json": schema},
                            }
                        ]
                    }
                }
            ).encode()
        ]
    )
    chat_stream = TrackingStream([assistant_message(None)])
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, stream=list_stream if len(requests) == 1 else chat_stream)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = AssistantClient(
        auth=AuthManager(token="token"),
        runtime_url="https://runtime.test",
        http_client=http_client,
    )

    tools = client.list_tools()
    events = list(client.chat("hello", tools=tools))

    forwarded = json.loads(requests[1].content)["conversationState"]["currentMessage"][
        "userInputMessage"
    ]["userInputMessageContext"]["tools"]
    assert forwarded == [tools[0].to_api()]
    assert tools[0].input_schema == schema
    assert [event.event_type for event in events] == ["end"]
    assert list_stream.closed and chat_stream.closed
    client.close()


def test_chat_forwards_tool_specs_and_raw_mappings_without_mutating_caller() -> None:
    requests: list[httpx.Request] = []
    stream = TrackingStream([assistant_message(None)])

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, stream=stream)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = AssistantClient(
        auth=AuthManager(token="token"),
        runtime_url="https://runtime.test",
        http_client=http_client,
    )
    tool = ToolSpec.from_api(
        {
            "name": "search",
            "description": "Search",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {"q": {"type": "string", "enum": ["a", "b"]}},
                }
            },
        }
    )
    raw_tool = {"rawProviderTool": {"nested": ["unchanged"]}}

    events = list(client.chat("hello", tools=[tool, raw_tool]))

    forwarded = json.loads(requests[0].content)["conversationState"]["currentMessage"][
        "userInputMessage"
    ]["userInputMessageContext"]["tools"]
    assert forwarded == [tool.to_api(), raw_tool]
    assert raw_tool == {"rawProviderTool": {"nested": ["unchanged"]}}
    assert [event.event_type for event in events] == ["end"]
    assert events[0].done
    assert stream.closed
    client.close()


def test_chat_surfaces_non_assistant_wire_event_and_normal_end() -> None:
    raw_body = {"name": "search", "arguments": {"q": "term"}}
    stream = TrackingStream(
        [
            event_message("toolCallEvent", raw_body),
            assistant_message("answer"),
            assistant_message(None),
        ]
    )
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    )
    client = AssistantClient(
        auth=AuthManager(token="token"),
        runtime_url="https://runtime.test",
        http_client=http_client,
    )

    events = list(client.chat("hello"))

    assert [event.event_type for event in events] == ["toolCallEvent", "content", "end"]
    assert events[0].raw == raw_body
    assert events[1].content == "answer"
    assert events[2].done
    assert stream.closed
    client.close()


def test_chat_terminal_event_finalizes_trailing_bytes_and_closes() -> None:
    stream = TrackingStream([assistant_message(None) + b"trailing"])
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    )
    client = AssistantClient(
        auth=AuthManager(token="token"),
        runtime_url="https://runtime.test",
        http_client=http_client,
    )

    with pytest.raises(StreamError, match="[Tt]runcated"):
        list(client.chat("hello"))

    assert stream.closed
    client.close()


def test_chat_maps_corrupt_upstream_json_to_stream_error() -> None:
    stream = TrackingStream([raw_event_message("assistantResponseEvent", b"{oops")])
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    )
    client = AssistantClient(
        auth=AuthManager(token="token"),
        runtime_url="https://runtime.test",
        http_client=http_client,
    )

    with pytest.raises(StreamError, match="not valid JSON"):
        list(client.chat("hello"))

    assert stream.closed
    client.close()


def test_chat_rejects_a_non_object_event_body() -> None:
    stream = TrackingStream([raw_event_message("assistantResponseEvent", b'["not", "json"]')])
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    )
    client = AssistantClient(
        auth=AuthManager(token="token"),
        runtime_url="https://runtime.test",
        http_client=http_client,
    )

    with pytest.raises(StreamError, match="must be a JSON object"):
        list(client.chat("hello"))

    assert stream.closed
    client.close()


def test_chat_rejects_non_string_assistant_content() -> None:
    stream = TrackingStream([event_message("assistantResponseEvent", {"content": {"nested": 1}})])
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    )
    client = AssistantClient(
        auth=AuthManager(token="token"),
        runtime_url="https://runtime.test",
        http_client=http_client,
    )

    with pytest.raises(StreamError, match="content must be a string"):
        list(client.chat("hello"))

    assert stream.closed
    client.close()


def test_chat_ignores_a_non_string_model_id() -> None:
    stream = TrackingStream(
        [
            event_message("assistantResponseEvent", {"content": "ok", "modelId": 7}),
            assistant_message(None),
        ]
    )
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    )
    client = AssistantClient(
        auth=AuthManager(token="token"),
        runtime_url="https://runtime.test",
        http_client=http_client,
    )

    events = list(client.chat("hello"))

    assert events[0].content == "ok"
    assert events[0].model_id is None
    assert stream.closed
    client.close()


def test_public_profile_arn_is_used_for_upstream_requests() -> None:
    auth = AuthManager(token="token", profile_arn="arn:test", source="explicit")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"models": []})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = AssistantClient(auth=auth, http_client=http_client)

    assert auth.profile_arn == "arn:test"
    assert auth.source == "explicit"
    assert client.list_models() == []
    assert json.loads(requests[0].content)["profileArn"] == "arn:test"
    client.close()

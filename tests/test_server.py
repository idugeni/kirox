"""HTTP adapter and server contract tests."""

from __future__ import annotations

import json
import re
from collections.abc import Generator
from typing import Any, Optional, cast

import pytest
from flask import Flask, Response
from flask.testing import FlaskClient

from kirox.core.auth import AuthManager
from kirox.core.client import AssistantClient
from kirox.core.errors import APIError, AuthenticationError
from kirox.core.models import ModelInfo, StreamEvent, TokenLimits
from kirox.service.server import create_app
from kirox.utils.config import Config


class TrackingEvents:
    def __init__(
        self,
        events: list[StreamEvent],
        *,
        error: Exception | None = None,
    ) -> None:
        self._events = events
        self._error = error
        self._index = 0
        self._raised = False
        self.consumed = 0
        self.closed = False

    def __iter__(self) -> TrackingEvents:
        return self

    def __next__(self) -> StreamEvent:
        if self._index < len(self._events):
            event = self._events[self._index]
            self._index += 1
            self.consumed += 1
            return event
        if self._error is not None and not self._raised:
            self._raised = True
            raise self._error
        raise StopIteration

    def close(self) -> None:
        self.closed = True


class FakeAssistantClient(AssistantClient):
    def __init__(self) -> None:
        self._fake_auth = AuthManager(
            token="test-token",
            profile_arn="test-profile",
            source="explicit",
        )
        self.simple_response = "Hello from test"
        self.simple_error: Exception | None = None
        self.list_error: Exception | None = None
        self.stream = TrackingEvents([StreamEvent(event_type="content", content="streamed")])
        self.simple_calls: list[tuple[str, str]] = []
        self.stream_calls: list[tuple[str, str]] = []
        self.list_calls = 0

    @property
    def auth(self) -> AuthManager:
        return self._fake_auth

    def list_models(self) -> list[ModelInfo]:
        self.list_calls += 1
        if self.list_error is not None:
            raise self.list_error
        return [
            ModelInfo(
                model_id="test-model",
                model_name="Test",
                description="Test model",
                rate_multiplier=1.0,
                rate_unit="Credit",
                token_limits=TokenLimits(
                    max_input_tokens=100000,
                    max_output_tokens=64000,
                ),
            )
        ]

    def chat(
        self,
        message: str,
        *,
        model_id: str = "auto",
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> Generator[StreamEvent, None, None]:
        del tools
        self.stream_calls.append((message, model_id))
        return cast(Generator[StreamEvent, None, None], self.stream)

    def chat_simple(self, message: str, model_id: str = "auto") -> str:
        self.simple_calls.append((message, model_id))
        if self.simple_error is not None:
            raise self.simple_error
        return self.simple_response


@pytest.fixture
def fake_client() -> FakeAssistantClient:
    return FakeAssistantClient()


@pytest.fixture
def app(fake_client: FakeAssistantClient) -> Flask:
    application = create_app(Config(), client=fake_client)
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


def openai_payload(**updates: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "Hello"}],
    }
    payload.update(updates)
    return payload


def anthropic_payload(**updates: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": "test-model",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "Hello"}],
    }
    payload.update(updates)
    return payload


def dispatch_stream(app: Flask, path: str, payload: dict[str, Any]) -> Response:
    with app.test_request_context(path, method="POST", json=payload):
        response = app.full_dispatch_request()
    return response


def read_stream(response: Response) -> str:
    try:
        return "".join(
            chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            for chunk in response.response
        )
    finally:
        response.close()


def event_names(stream: str) -> list[str]:
    return re.findall(r"^event: ([^\r\n]+)$", stream, flags=re.MULTILINE)


def openai_stream_payloads(stream: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for line in stream.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        payloads.append(json.loads(line.removeprefix("data: ")))
    return payloads


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "localhost:1",
        "localhost:65535",
        "127.0.0.1",
        "127.0.0.1:8000",
        "[::1]",
        "[::1]:443",
    ],
)
def test_loopback_host_headers_are_accepted(client: FlaskClient, host: str) -> None:
    response = client.get("/health", headers={"Host": host})

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_default_flask_test_host_is_accepted(client: FlaskClient) -> None:
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize(
    "host",
    [
        "example.com",
        "192.168.1.1",
        "",
        ":8000",
        "localhost:",
        "localhost:not-a-port",
        "localhost:0",
        "localhost:65536",
        "localhost:99999999999999999999",
        "user@localhost",
        "localhost/path",
        "localhost,example.com",
        "local host",
        "[::1]:",
        "[::1]:not-a-port",
        "[::1]:0",
        "[::1]:65536",
        "[::1]extra",
        "[127.0.0.1]",
        "::1",
        "::ffff:127.0.0.1",
        "::1:8000",
    ],
)
def test_non_loopback_and_malformed_host_headers_are_rejected(
    client: FlaskClient, host: str
) -> None:
    with client.application.test_request_context("/health", headers={"Host": host}):
        response = client.application.full_dispatch_request()

    assert response.status_code == 400
    assert response.is_json
    assert response.get_json() == {"error": "Invalid Host header"}


@pytest.mark.parametrize(
    ("path", "expected_envelope"),
    [
        ("/api/chat", "native"),
        ("/v1/chat/completions", "openai"),
        ("/v1/messages", "anthropic"),
    ],
)
def test_oversized_json_returns_controlled_413_without_calling_upstream(
    client: FlaskClient,
    fake_client: FakeAssistantClient,
    path: str,
    expected_envelope: str,
) -> None:
    oversized_body = b'"' + (b"x" * (1024 * 1024)) + b'"'

    response = client.post(path, data=oversized_body, content_type="application/json")

    assert response.status_code == 413
    assert response.is_json
    body = response.get_json()
    if expected_envelope == "native":
        assert body == {"error": "Request body exceeds 1 MiB limit"}
    else:
        if expected_envelope == "anthropic":
            assert body["type"] == "error"
        details = body["error"]
        assert details == {
            "type": "invalid_request_error",
            "message": "Request body exceeds 1 MiB limit",
            "param": "body",
            "code": "request_too_large",
        }
    assert fake_client.simple_calls == []
    assert fake_client.stream_calls == []
    assert fake_client.list_calls == 0


def test_legacy_routes_use_the_injected_client(
    client: FlaskClient, fake_client: FakeAssistantClient
) -> None:
    assert client.get("/health").get_json()["status"] == "ok"
    root = client.get("/").get_json()
    assert root["endpoints"]["openai"] == "/v1/*"
    assert root["endpoints"]["anthropic"] == "/v1/messages"

    assert client.get("/api/models").get_json() == {
        "models": [{"id": "test-model", "name": "Test"}]
    }
    native = client.post("/api/chat", json={"message": "legacy", "model": "test-model"})
    assert native.status_code == 200
    assert native.get_json() == {"response": "Hello from test"}
    assert fake_client.simple_calls[-1] == ("legacy", "test-model")

    token_status = client.get("/api/token/status")
    assert token_status.status_code == 200
    assert token_status.get_json() == {
        "authenticated": True,
        "has_profile": True,
        "source": "explicit",
    }

    models = client.get("/v1/models")
    assert models.status_code == 200
    assert models.get_json()["data"][0]["id"] == "test-model"
    assert fake_client.list_calls == 2


def test_native_chat_still_defaults_to_auto_model(
    client: FlaskClient, fake_client: FakeAssistantClient
) -> None:
    response = client.post("/api/chat", json={"message": "Hello"})

    assert response.status_code == 200
    assert fake_client.simple_calls == [("Hello", "auto")]


def test_openai_history_and_text_blocks_become_canonical_transcript(
    client: FlaskClient, fake_client: FakeAssistantClient
) -> None:
    response = client.post(
        "/v1/chat/completions",
        json=openai_payload(
            messages=[
                {"role": "system", "content": "Be exact."},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "First "},
                        {"type": "text", "text": "question"},
                    ],
                },
                {"role": "assistant", "content": "First answer"},
                {"role": "user", "content": "Follow-up"},
            ]
        ),
    )

    assert response.status_code == 200
    assert fake_client.simple_calls == [
        (
            "SYSTEM:\nBe exact.\n\nUSER:\nFirst question\n\n"
            "ASSISTANT:\nFirst answer\n\nUSER:\nFollow-up",
            "test-model",
        )
    ]
    body = response.get_json()
    assert body["choices"][0]["message"]["content"] == "Hello from test"
    assert "usage" not in body


def test_anthropic_system_history_and_blocks_become_canonical_transcript(
    client: FlaskClient, fake_client: FakeAssistantClient
) -> None:
    response = client.post(
        "/v1/messages",
        json=anthropic_payload(
            system=[{"type": "text", "text": "System policy"}],
            messages=[
                {"role": "user", "content": "Question"},
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Answer"}],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Follow"},
                        {"type": "text", "text": " up"},
                    ],
                },
            ],
        ),
    )

    assert response.status_code == 200
    assert fake_client.simple_calls == [
        (
            "SYSTEM:\nSystem policy\n\nUSER:\nQuestion\n\nASSISTANT:\nAnswer\n\nUSER:\nFollow up",
            "test-model",
        )
    ]
    body = response.get_json()
    assert body["content"] == [{"type": "text", "text": "Hello from test"}]
    assert "usage" not in body
    assert "max_tokens is accepted" in response.headers["Warning"]


@pytest.mark.parametrize("path", ["/v1/chat/completions", "/v1/messages"])
def test_malformed_json_is_a_field_specific_400(client: FlaskClient, path: str) -> None:
    response = client.post(path, data='{"model":', content_type="application/json")

    assert response.status_code == 400
    body = response.get_json()
    details = body["error"]
    assert details["param"] == "body"
    assert details["code"] == "invalid_json"


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"messages": [{"role": "user", "content": "Hello"}]}, "model"),
        (openai_payload(model=7), "model"),
        (openai_payload(messages="Hello"), "messages"),
        (openai_payload(messages=[]), "messages"),
        (
            openai_payload(messages=[{"role": "tool", "content": "result"}]),
            "messages[0].role",
        ),
        (
            openai_payload(messages=[{"role": "assistant", "content": "answer"}]),
            "messages[0].role",
        ),
        (
            openai_payload(messages=[{"role": "user", "content": "  "}]),
            "messages[0].content",
        ),
        (openai_payload(stream="true"), "stream"),
        (openai_payload(max_tokens=0), "max_tokens"),
    ],
)
def test_openai_invalid_contract_is_field_specific(
    client: FlaskClient, payload: dict[str, Any], field: str
) -> None:
    response = client.post("/v1/chat/completions", json=payload)

    assert response.status_code == 400
    details = response.get_json()["error"]
    assert details["type"] == "invalid_request_error"
    assert details["param"] == field
    assert field in details["message"]


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        (openai_payload(tools=[]), "tools"),
        (openai_payload(temperature=0), "temperature"),
        (openai_payload(tool_choice="none"), "tool_choice"),
        (openai_payload(top_p=1), "top_p"),
        (
            openai_payload(
                messages=[
                    {
                        "role": "assistant",
                        "content": "answer",
                        "tool_calls": [],
                    },
                    {"role": "user", "content": "next"},
                ]
            ),
            "messages[0].tool_calls",
        ),
        (
            openai_payload(
                messages=[
                    {
                        "role": "user",
                        "content": [{"type": "image_url", "image_url": {"url": "x"}}],
                    }
                ]
            ),
            "messages[0].content[0].type",
        ),
        (
            openai_payload(
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Hello",
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                    }
                ]
            ),
            "messages[0].content[0].cache_control",
        ),
    ],
)
def test_openai_unsupported_semantics_are_never_ignored(
    client: FlaskClient, payload: dict[str, Any], field: str
) -> None:
    response = client.post("/v1/chat/completions", json=payload)

    assert response.status_code == 400
    details = response.get_json()["error"]
    assert details["param"] == field
    assert details["code"].startswith("unsupported_")


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        (anthropic_payload(tools=[]), "tools"),
        (anthropic_payload(temperature=0.2), "temperature"),
        (anthropic_payload(tool_choice={"type": "auto"}), "tool_choice"),
        (
            anthropic_payload(
                messages=[
                    {
                        "role": "user",
                        "content": [{"type": "image", "source": {"data": "secret"}}],
                    }
                ]
            ),
            "messages[0].content[0].type",
        ),
    ],
)
def test_anthropic_unsupported_semantics_are_field_specific(
    client: FlaskClient, payload: dict[str, Any], field: str
) -> None:
    response = client.post("/v1/messages", json=payload)

    assert response.status_code == 400
    body = response.get_json()
    assert body["type"] == "error"
    assert body["error"]["param"] == field


@pytest.mark.parametrize(
    ("error", "status", "message"),
    [
        (AuthenticationError("secret auth detail"), 401, "Authentication required"),
        (
            APIError("secret upstream body", 503, {"token": "secret"}),
            502,
            "Upstream service request failed",
        ),
        (
            APIError("secret upstream body", 400, {"reason": "INVALID_MODEL_ID"}),
            400,
            "The requested model is not available for this account",
        ),
        (
            APIError("secret upstream body", 400, {"reason": "SOMETHING_NEW"}),
            400,
            "Upstream rejected the request as invalid",
        ),
        (
            APIError("secret upstream body", 400, "plain text body"),
            400,
            "Upstream rejected the request as invalid",
        ),
        (APIError("secret upstream body", 400), 400, "Upstream rejected the request as invalid"),
        (RuntimeError("secret internal detail"), 500, "Internal server error"),
    ],
)
def test_openai_runtime_errors_are_sanitized(
    client: FlaskClient,
    fake_client: FakeAssistantClient,
    error: Exception,
    status: int,
    message: str,
) -> None:
    fake_client.simple_error = error

    response = client.post("/v1/chat/completions", json=openai_payload())

    assert response.status_code == status
    rendered = response.get_data(as_text=True)
    assert "secret" not in rendered
    assert response.get_json()["error"]["message"] == message


def test_unavailable_model_is_a_client_error_not_a_gateway_failure(
    client: FlaskClient, fake_client: FakeAssistantClient
) -> None:
    # An upstream INVALID_MODEL_ID is the caller's model choice, so reporting it
    # as 502 would blame the gateway for a request the caller can correct.
    fake_client.simple_error = APIError("Error 400", 400, {"reason": "INVALID_MODEL_ID"})

    anthropic = client.post("/v1/messages", json=anthropic_payload())
    native = client.post("/api/chat", json={"message": "Hello"})

    assert anthropic.status_code == 400
    assert anthropic.get_json() == {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "The requested model is not available for this account",
        },
    }
    assert native.status_code == 400
    assert native.get_json() == {"error": "The requested model is not available for this account"}


def test_unavailable_model_streams_a_terminal_client_error(
    app: Flask, fake_client: FakeAssistantClient
) -> None:
    fake_client.stream = TrackingEvents(
        [], error=APIError("Error 400", 400, {"reason": "INVALID_MODEL_ID"})
    )

    response = dispatch_stream(app, "/v1/chat/completions", openai_payload(stream=True))
    stream = read_stream(response)

    assert response.status_code == 200
    errors = [payload for payload in openai_stream_payloads(stream) if "error" in payload]
    assert errors == [
        {
            "error": {
                "type": "invalid_request_error",
                "message": "The requested model is not available for this account",
            }
        }
    ]
    assert stream.count("data: [DONE]") == 1


def test_anthropic_and_native_errors_keep_compatible_envelopes_but_hide_details(
    client: FlaskClient, fake_client: FakeAssistantClient
) -> None:
    fake_client.simple_error = APIError("credential=secret", 500)
    anthropic = client.post("/v1/messages", json=anthropic_payload())
    native = client.post("/api/chat", json={"message": "Hello"})

    assert anthropic.status_code == 502
    assert anthropic.get_json() == {
        "type": "error",
        "error": {"type": "api_error", "message": "Upstream service request failed"},
    }
    assert native.status_code == 502
    assert native.get_json() == {"error": "Upstream service request failed"}
    assert "secret" not in anthropic.get_data(as_text=True)
    assert "secret" not in native.get_data(as_text=True)


def test_model_errors_are_sanitized(client: FlaskClient, fake_client: FakeAssistantClient) -> None:
    fake_client.list_error = AuthenticationError("token=secret")

    response = client.get("/v1/models")

    assert response.status_code == 401
    assert response.get_json()["error"]["message"] == "Authentication required"
    assert "secret" not in response.get_data(as_text=True)


def test_openai_stream_is_context_independent_incremental_and_consistent(
    app: Flask, fake_client: FakeAssistantClient
) -> None:
    fake_client.stream = TrackingEvents(
        [
            StreamEvent(event_type="content", content="first"),
            StreamEvent(event_type="content", content="second"),
        ]
    )
    response = dispatch_stream(app, "/v1/chat/completions", openai_payload(stream=True))

    assert fake_client.stream.consumed == 0
    stream = read_stream(response)

    assert response.headers["Cache-Control"] == "no-cache, no-transform"
    assert response.headers["X-Accel-Buffering"] == "no"
    assert stream.count("data: [DONE]") == 1
    payloads = openai_stream_payloads(stream)
    content_payloads = [payload for payload in payloads if "error" not in payload][:-1]
    assert [item["choices"][0]["delta"]["content"] for item in content_payloads] == [
        "first",
        "second",
    ]
    assert len({item["id"] for item in payloads}) == 1
    assert len({item["created"] for item in payloads}) == 1
    assert payloads[-1]["choices"][0]["finish_reason"] == "stop"
    assert "usage" not in stream
    assert fake_client.stream.closed


def test_openai_first_chunk_does_not_prebuffer_and_disconnect_closes_upstream(
    app: Flask, fake_client: FakeAssistantClient
) -> None:
    fake_client.stream = TrackingEvents(
        [
            StreamEvent(event_type="content", content="first"),
            StreamEvent(event_type="content", content="second"),
        ]
    )
    response = dispatch_stream(app, "/v1/chat/completions", openai_payload(stream=True))
    iterator = response.iter_encoded()

    first_chunk = next(iterator).decode("utf-8")
    assert "first" in first_chunk
    assert fake_client.stream.consumed == 1
    assert not fake_client.stream.closed

    response.close()
    assert fake_client.stream.closed
    assert fake_client.stream.consumed == 1


def test_openai_stream_error_is_sanitized_closed_and_terminated_once(
    app: Flask, fake_client: FakeAssistantClient
) -> None:
    fake_client.stream = TrackingEvents(
        [StreamEvent(event_type="content", content="first")],
        error=APIError("upstream secret body", 503),
    )
    response = dispatch_stream(app, "/v1/chat/completions", openai_payload(stream=True))

    stream = read_stream(response)

    assert "first" in stream
    assert "Upstream service request failed" in stream
    assert "secret" not in stream
    assert stream.count("data: [DONE]") == 1
    assert stream.count('"finish_reason":"stop"') == 0
    assert fake_client.stream.closed


def test_anthropic_stream_has_protocol_order_warning_and_one_terminator(
    app: Flask, fake_client: FakeAssistantClient
) -> None:
    fake_client.stream = TrackingEvents(
        [
            StreamEvent(event_type="content", content="first"),
            StreamEvent(event_type="content", content="second"),
        ]
    )
    response = dispatch_stream(app, "/v1/messages", anthropic_payload(stream=True))

    stream = read_stream(response)

    assert event_names(stream) == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert stream.count("event: message_stop") == 1
    assert "usage" not in stream
    assert "max_tokens is accepted" in response.headers["Warning"]
    assert response.headers["Cache-Control"] == "no-cache, no-transform"
    assert response.headers["X-Accel-Buffering"] == "no"
    assert fake_client.stream.closed


def test_anthropic_stream_error_is_the_only_terminal_event(
    app: Flask, fake_client: FakeAssistantClient
) -> None:
    fake_client.stream = TrackingEvents(
        [StreamEvent(event_type="content", content="partial")],
        error=RuntimeError("internal secret"),
    )
    response = dispatch_stream(app, "/v1/messages", anthropic_payload(stream=True))

    stream = read_stream(response)

    assert event_names(stream) == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "error",
    ]
    assert "message_stop" not in stream
    assert "Internal server error" in stream
    assert "secret" not in stream
    assert fake_client.stream.closed


def test_max_tokens_is_validated_but_not_claimed_as_enforced(client: FlaskClient) -> None:
    response = client.post(
        "/v1/chat/completions",
        json=openai_payload(max_tokens=32),
    )

    assert response.status_code == 200
    assert "max_tokens is accepted" in response.headers["Warning"]
    assert "usage" not in response.get_json()


def test_native_chat_rejects_bad_json_and_empty_message(client: FlaskClient) -> None:
    malformed = client.post("/api/chat", data="{", content_type="application/json")
    empty = client.post("/api/chat", json={"message": " "})

    assert malformed.status_code == 400
    assert malformed.get_json() == {"error": "body must be a valid JSON object"}
    assert empty.status_code == 400
    assert empty.get_json() == {"error": "message required"}

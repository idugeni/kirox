"""Local HTTP API server — OpenAI & Anthropic compatible bridge."""

from __future__ import annotations

import ipaddress
import json
import logging
import secrets
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any, Literal, Optional

import httpx
from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.serving import BaseWSGIServer, make_server

from kirox._version import __version__
from kirox.core.client import AssistantClient
from kirox.core.errors import APIError, AuthenticationError, StreamError
from kirox.service._http_adapter import (
    RequestValidationError,
    TextChatRequest,
    parse_anthropic_request,
    parse_openai_request,
)
from kirox.utils.config import Config
from kirox.utils.net import is_loopback_host

logger = logging.getLogger(__name__)

CONTROL_SHUTDOWN_PATH = "/_kirox/shutdown"
CONTROL_TOKEN_HEADER = "X-Kirox-Control-Token"
APP_CLIENT_CLOSER = "kirox_close_owned_client"
_MAX_JSON_CONTENT_LENGTH = 1024 * 1024
_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
}

Provider = Literal["openai", "anthropic"]


def _parse_host_header(value: str) -> str | None:
    if not value or any(character.isspace() for character in value):
        return None
    if any(character in value for character in "@/,\\?#%"):
        return None

    port_text: str | None = None
    if value.startswith("["):
        closing_bracket = value.find("]")
        if closing_bracket < 0 or value.find("]", closing_bracket + 1) >= 0:
            return None
        host = value[1:closing_bracket]
        remainder = value[closing_bracket + 1 :]
        if not host or "[" in host:
            return None
        try:
            if not isinstance(ipaddress.ip_address(host), ipaddress.IPv6Address):
                return None
        except ValueError:
            return None
        if remainder:
            if not remainder.startswith(":"):
                return None
            port_text = remainder[1:]
    else:
        if "[" in value or "]" in value or value.count(":") > 1:
            return None
        if value.count(":") == 1:
            host, port_text = value.rsplit(":", 1)
        else:
            host = value

    if not host:
        return None
    if port_text is not None:
        if len(port_text) > 5 or not port_text.isascii() or not port_text.isdigit():
            return None
        port = int(port_text)
        if not 1 <= port <= 65535:
            return None
    return host if is_loopback_host(host) else None


def _format_url(host: str, port: int) -> str:
    normalized = host.strip("[]")
    rendered_host = f"[{normalized}]" if ":" in normalized else normalized
    return f"http://{rendered_host}:{port}"


class ManagedHTTPServer:
    """A bound Werkzeug server with explicit, idempotent thread ownership."""

    def __init__(self, app: Flask, *, host: str, port: int) -> None:
        if not is_loopback_host(host):
            raise ValueError("Kirox HTTP server must bind to a loopback address")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("Kirox HTTP server port must be between 0 and 65535")
        self._host = host
        self._server: BaseWSGIServer = make_server(host, port, app, threaded=True)
        self._thread: Optional[threading.Thread] = None
        self._done = threading.Event()
        self._closed = False
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        return _format_url(self._host, self._server.server_port)

    @property
    def port(self) -> int:
        return self._server.server_port

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("HTTP server is closed")
            if self._thread is not None:
                if self._thread.is_alive():
                    return
                raise RuntimeError("HTTP server cannot be restarted")
            self._done.clear()
            thread = threading.Thread(
                target=self._serve,
                name="kirox-http-server",
                daemon=False,
            )
            self._thread = thread
            try:
                thread.start()
            except BaseException:
                self._thread = None
                self._done.set()
                self._server.server_close()
                self._closed = True
                raise

    def _serve(self) -> None:
        try:
            self._server.serve_forever()
        except Exception:
            logger.exception("Kirox HTTP server stopped unexpectedly")
        finally:
            self._done.set()

    def stop(self) -> None:
        with self._lock:
            if self._closed:
                return
            thread = self._thread
            if (
                thread is not None
                and thread.is_alive()
                and thread is not threading.current_thread()
            ):
                self._server.shutdown()
                thread.join()
            self._server.server_close()
            self._closed = True
            self._done.set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._done.wait(timeout)


def _request_payload() -> Any:
    if not request.is_json:
        return None
    return request.get_json(silent=True)


def _mapped_error(error: Exception) -> tuple[int, str, str]:
    if isinstance(error, AuthenticationError) or (
        isinstance(error, APIError) and error.status in {401, 403}
    ):
        return 401, "authentication_error", "Authentication required"
    if isinstance(error, (APIError, StreamError, httpx.HTTPError)):
        return 502, "api_error", "Upstream service request failed"
    return 500, "api_error", "Internal server error"


def _log_mapped_error(error: Exception, status: int) -> None:
    if status == 500:
        logger.exception("Internal HTTP adapter failure (%s)", type(error).__name__)
    else:
        logger.warning("HTTP adapter mapped %s to status %s", type(error).__name__, status)


def _provider_error_payload(provider: Provider, error_type: str, message: str) -> dict[str, Any]:
    error = {"type": error_type, "message": message}
    if provider == "anthropic":
        return {"type": "error", "error": error}
    return {"error": error}


def _provider_runtime_error(provider: Provider, error: Exception) -> tuple[Response, int]:
    status, error_type, message = _mapped_error(error)
    _log_mapped_error(error, status)
    return jsonify(_provider_error_payload(provider, error_type, message)), status


def _native_runtime_error(error: Exception) -> tuple[Response, int]:
    status, _, message = _mapped_error(error)
    _log_mapped_error(error, status)
    return jsonify({"error": message}), status


def _validation_error(provider: Provider, error: RequestValidationError) -> tuple[Response, int]:
    details = {
        "type": "invalid_request_error",
        "message": str(error),
        "param": error.field,
        "code": error.code,
    }
    if provider == "anthropic":
        return jsonify({"type": "error", "error": details}), 400
    return jsonify({"error": details}), 400


def _apply_warnings(response: Response, warnings: tuple[str, ...]) -> Response:
    for warning in warnings:
        response.headers.add("Warning", f'299 kirox "{warning}"')
    return response


def _json_data(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _data_event(value: dict[str, Any]) -> str:
    return f"data: {_json_data(value)}\n\n"


def _named_event(name: str, value: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {_json_data(value)}\n\n"


def _close_upstream(upstream: Any) -> None:
    if upstream is None:
        return
    close = getattr(upstream, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        logger.warning("Failed to close upstream stream", exc_info=True)


def _openai_stream_response(
    client: AssistantClient,
    adapted: TextChatRequest,
    *,
    chat_id: str,
    created: int,
) -> Response:
    def generate() -> Iterator[str]:
        upstream: Any = None
        failure: tuple[str, str] | None = None
        try:
            upstream = client.chat(adapted.transcript, model_id=adapted.model)
            for event in upstream:
                if event.done:
                    break
                if not event.content:
                    continue
                yield _data_event(
                    {
                        "id": chat_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": adapted.model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": event.content},
                                "finish_reason": None,
                            }
                        ],
                    }
                )
        except GeneratorExit:
            raise
        except Exception as error:
            status, error_type, message = _mapped_error(error)
            _log_mapped_error(error, status)
            failure = (error_type, message)
        finally:
            _close_upstream(upstream)

        if failure is None:
            yield _data_event(
                {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": adapted.model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
            )
        else:
            yield _data_event(_provider_error_payload("openai", *failure))
        yield "data: [DONE]\n\n"

    response = Response(generate(), content_type="text/event-stream; charset=utf-8")
    response.headers.update(_SSE_HEADERS)
    return _apply_warnings(response, adapted.warnings)


def _anthropic_stream_response(
    client: AssistantClient,
    adapted: TextChatRequest,
    *,
    message_id: str,
) -> Response:
    def generate() -> Iterator[str]:
        upstream: Any = None
        failure: tuple[str, str] | None = None
        try:
            yield _named_event(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": message_id,
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": adapted.model,
                        "stop_reason": None,
                        "stop_sequence": None,
                    },
                },
            )
            yield _named_event(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            )
            upstream = client.chat(adapted.transcript, model_id=adapted.model)
            for event in upstream:
                if event.done:
                    break
                if not event.content:
                    continue
                yield _named_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": event.content},
                    },
                )
        except GeneratorExit:
            raise
        except Exception as error:
            status, error_type, message = _mapped_error(error)
            _log_mapped_error(error, status)
            failure = (error_type, message)
        finally:
            _close_upstream(upstream)

        if failure is not None:
            yield _named_event("error", _provider_error_payload("anthropic", *failure))
            return

        yield _named_event("content_block_stop", {"type": "content_block_stop", "index": 0})
        yield _named_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            },
        )
        yield _named_event("message_stop", {"type": "message_stop"})

    response = Response(generate(), content_type="text/event-stream; charset=utf-8")
    response.headers.update(_SSE_HEADERS)
    return _apply_warnings(response, adapted.warnings)


def create_app(
    config: Optional[Config] = None,
    *,
    client: Optional[AssistantClient] = None,
    shutdown_callback: Optional[Callable[[], None]] = None,
    control_token: Optional[str] = None,
) -> Flask:
    app = Flask(__name__)
    app.config["CONFIG"] = config or Config()
    app.config["MAX_CONTENT_LENGTH"] = _MAX_JSON_CONTENT_LENGTH
    shared_client = client
    owns_client = False
    closed = False
    client_lock = threading.Lock()

    @app.before_request
    def validate_host() -> Any:
        if _parse_host_header(request.headers.get("Host", "")) is None:
            return jsonify({"error": "Invalid Host header"}), 400
        return None

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_error: RequestEntityTooLarge) -> tuple[Response, int]:
        message = "Request body exceeds 1 MiB limit"
        details = {
            "type": "invalid_request_error",
            "message": message,
            "param": "body",
            "code": "request_too_large",
        }
        if request.path == "/v1/messages":
            return jsonify({"type": "error", "error": details}), 413
        if request.path == "/v1/chat/completions":
            return jsonify({"error": details}), 413
        return jsonify({"error": message}), 413

    def get_client() -> AssistantClient:
        nonlocal shared_client, owns_client
        with client_lock:
            if closed:
                raise RuntimeError("Kirox HTTP application client is closed")
            if shared_client is None:
                from kirox.core.auth import AuthManager

                cfg = app.config["CONFIG"]
                # resolve() owns the documented precedence, including treating a
                # config token that duplicates KIROX_TOKEN as environment
                # provenance, so the reported source cannot misattribute it.
                auth = AuthManager.resolve(config=cfg)
                shared_client = AssistantClient(auth=auth, region=cfg.region)
                owns_client = True
            return shared_client

    def close_owned_client() -> None:
        """Close only a client this app created itself, once and for good.

        An injected client belongs to the managed service, which closes it as
        part of its own shutdown ordering.
        """
        nonlocal shared_client, owns_client, closed
        with client_lock:
            closed = True
            if not owns_client or shared_client is None:
                return
            closing, shared_client, owns_client = shared_client, None, False
        try:
            closing.close()
        except Exception:
            logger.warning("Failed to close app-owned Kirox client", exc_info=True)

    app.extensions[APP_CLIENT_CLOSER] = close_owned_client

    # ── Health & Info ──────────────────────────────────────────────
    @app.route("/health", methods=["GET"])
    def health() -> Any:
        return jsonify({"status": "ok", "version": __version__, "bridge": "kirox"})

    @app.route("/", methods=["GET"])
    def root() -> Any:
        return jsonify(
            {
                "service": "Kirox Bridge",
                "version": __version__,
                "endpoints": {
                    "openai": "/v1/*",
                    "anthropic": "/v1/messages",
                    "kirox": "/api/*",
                },
            }
        )

    @app.route(CONTROL_SHUTDOWN_PATH, methods=["POST"])
    def internal_shutdown() -> Any:
        if shutdown_callback is None or not control_token:
            return jsonify({"error": "not found"}), 404
        remote_address = request.remote_addr or ""
        if not is_loopback_host(remote_address):
            return jsonify({"error": "forbidden"}), 403
        supplied_token = request.headers.get(CONTROL_TOKEN_HEADER, "")
        if not secrets.compare_digest(
            supplied_token.encode("utf-8"),
            control_token.encode("utf-8"),
        ):
            return jsonify({"error": "forbidden"}), 403
        try:
            shutdown_callback()
        except Exception as error:
            return _native_runtime_error(error)
        return jsonify({"status": "stopped"})

    # ── Kirox Native API ──────────────────────────────────────────
    @app.route("/api/models", methods=["GET"])
    def api_models() -> Any:
        try:
            models = get_client().list_models()
        except Exception as error:
            return _native_runtime_error(error)
        return jsonify(
            {"models": [{"id": model.model_id, "name": model.model_name} for model in models]}
        )

    @app.route("/api/chat", methods=["POST"])
    def api_chat() -> Any:
        data = _request_payload()
        if not isinstance(data, dict):
            return jsonify({"error": "body must be a valid JSON object"}), 400
        message = data.get("message")
        if not isinstance(message, str) or not message.strip():
            return jsonify({"error": "message required"}), 400
        model = data.get("model", "auto")
        if not isinstance(model, str) or not model.strip():
            return jsonify({"error": "model must be a non-empty string"}), 400
        try:
            response_text = get_client().chat_simple(message, model_id=model.strip())
        except Exception as error:
            return _native_runtime_error(error)
        return jsonify({"response": response_text})

    @app.route("/api/token/status", methods=["GET"])
    def api_token_status() -> Any:
        try:
            active_client = get_client()
            auth = active_client.auth
            payload = {
                "authenticated": auth.is_authenticated,
                "has_profile": auth.profile_arn is not None,
                "source": auth.source,
            }
        except Exception as error:
            return _native_runtime_error(error)
        return jsonify(payload)

    # ── OpenAI Compatible API ─────────────────────────────────────
    @app.route("/v1/models", methods=["GET"])
    def openai_models() -> Any:
        try:
            models = get_client().list_models()
        except Exception as error:
            return _provider_runtime_error("openai", error)
        created = int(time.time())
        return jsonify(
            {
                "object": "list",
                "data": [
                    {
                        "id": model.model_id,
                        "object": "model",
                        "created": created,
                        "owned_by": "kirox",
                    }
                    for model in models
                ],
            }
        )

    @app.route("/v1/chat/completions", methods=["POST"])
    def openai_chat() -> Any:
        try:
            adapted = parse_openai_request(_request_payload())
        except RequestValidationError as error:
            return _validation_error("openai", error)

        try:
            active_client = get_client()
            chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
            created = int(time.time())
            if adapted.stream:
                return _openai_stream_response(
                    active_client,
                    adapted,
                    chat_id=chat_id,
                    created=created,
                )
            response_text = active_client.chat_simple(adapted.transcript, model_id=adapted.model)
        except Exception as error:
            return _provider_runtime_error("openai", error)

        response = jsonify(
            {
                "id": chat_id,
                "object": "chat.completion",
                "created": created,
                "model": adapted.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": response_text},
                        "finish_reason": "stop",
                    }
                ],
            }
        )
        return _apply_warnings(response, adapted.warnings)

    # ── Anthropic Compatible API ──────────────────────────────────
    @app.route("/v1/messages", methods=["POST"])
    def anthropic_messages() -> Any:
        try:
            adapted = parse_anthropic_request(_request_payload())
        except RequestValidationError as error:
            return _validation_error("anthropic", error)

        try:
            active_client = get_client()
            message_id = f"msg_{uuid.uuid4().hex[:24]}"
            if adapted.stream:
                return _anthropic_stream_response(
                    active_client,
                    adapted,
                    message_id=message_id,
                )
            response_text = active_client.chat_simple(adapted.transcript, model_id=adapted.model)
        except Exception as error:
            return _provider_runtime_error("anthropic", error)

        response = jsonify(
            {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": response_text}],
                "model": adapted.model,
                "stop_reason": "end_turn",
                "stop_sequence": None,
            }
        )
        return _apply_warnings(response, adapted.warnings)

    return app


def close_app_owned_client(app: Flask) -> None:
    """Close the client an app created lazily, if it created one."""
    closer = app.extensions.get(APP_CLIENT_CLOSER)
    if callable(closer):
        closer()


def run_server(config: Optional[Config] = None) -> None:
    """Run the legacy blocking server entry point with managed socket cleanup."""
    cfg = config or Config()
    app = create_app(cfg)
    server = ManagedHTTPServer(app, host=cfg.server_host, port=cfg.server_port)
    server.start()
    try:
        server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        close_app_owned_client(app)

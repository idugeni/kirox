"""Local HTTP API server — OpenAI & Anthropic compatible bridge."""

from __future__ import annotations
import uuid
import time
from typing import Any, Optional
from flask import Flask, request, jsonify, Response
from kirox.core.client import AssistantClient
from kirox.utils.config import Config


def create_app(config: Optional[Config] = None) -> Flask:
    app = Flask(__name__)
    app.config["CONFIG"] = config or Config()
    client: Optional[AssistantClient] = None

    def get_client() -> AssistantClient:
        nonlocal client
        if client is None:
            cfg = app.config["CONFIG"]
            if cfg.token:
                from kirox.core.auth import AuthManager
                auth = AuthManager(token=cfg.token, profile_arn=cfg.profile_arn)
                client = AssistantClient(auth=auth, region=cfg.region)
            else:
                client = AssistantClient.from_cli_db(cfg.db_path, cfg.region)
        return client

    # ── Health & Info ──────────────────────────────────────────────
    @app.route("/health", methods=["GET"])
    def health() -> Any:
        return jsonify({"status": "ok", "version": "1.0.0", "bridge": "kirox"})

    @app.route("/", methods=["GET"])
    def root() -> Any:
        return jsonify({
            "service": "Kirox Bridge",
            "version": "1.0.0",
            "endpoints": {
                "openai": "/v1/*",
                "anthropic": "/v1/messages",
                "kirox": "/api/*",
            }
        })

    # ── Kirox Native API ──────────────────────────────────────────
    @app.route("/api/models", methods=["GET"])
    def api_models() -> Any:
        try:
            models = get_client().list_models()
            return jsonify({"models": [{"id": m.model_id, "name": m.model_name} for m in models]})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/chat", methods=["POST"])
    def api_chat() -> Any:
        data = request.get_json()
        if not data or "message" not in data:
            return jsonify({"error": "message required"}), 400
        try:
            response = get_client().chat_simple(data["message"], model_id=data.get("model", "auto"))
            return jsonify({"response": response})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/token/status", methods=["GET"])
    def api_token_status() -> Any:
        c = get_client()
        return jsonify({"authenticated": c.auth.is_authenticated, "has_profile": c.auth._profile_arn is not None})

    # ── OpenAI Compatible API ─────────────────────────────────────
    @app.route("/v1/models", methods=["GET"])
    def openai_models() -> Any:
        try:
            models = get_client().list_models()
            return jsonify({
                "object": "list",
                "data": [
                    {
                        "id": m.model_id,
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": "kirox",
                    }
                    for m in models
                ]
            })
        except Exception as e:
            return jsonify({"error": {"message": str(e), "type": "api_error"}}), 500

    @app.route("/v1/chat/completions", methods=["POST"])
    def openai_chat() -> Any:
        data = request.get_json()
        if not data:
            return jsonify({"error": {"message": "Request body required", "type": "invalid_request_error"}}), 400

        messages = data.get("messages", [])
        model = data.get("model", "auto")
        stream = data.get("stream", False)

        # Extract last user message
        content = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                break

        if not content:
            return jsonify({"error": {"message": "No user message found", "type": "invalid_request_error"}}), 400

        try:
            if stream:
                return _openai_stream_response(get_client(), content, model)
            else:
                response = get_client().chat_simple(content, model_id=model)
                return jsonify({
                    "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": response},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                })
        except Exception as e:
            return jsonify({"error": {"message": str(e), "type": "api_error"}}), 500

    def _openai_stream_response(client: AssistantClient, content: str, model: str) -> Response:
        def generate():
            chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
            for event in client.chat(content, model_id=model):
                if event.content:
                    chunk = {
                        "id": chat_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": event.content},
                                "finish_reason": None,
                            }
                        ],
                    }
                    yield f"data: {jsonify(chunk).get_data(as_text=True)}\n\n"
            # End stream
            yield f"data: {jsonify({'choices': [{'delta': {}, 'finish_reason': 'stop'}]}).get_data(as_text=True)}\n\n"
            yield "data: [DONE]\n\n"

        return Response(generate(), mimetype="text/event-stream")

    # ── Anthropic Compatible API ──────────────────────────────────
    @app.route("/v1/messages", methods=["POST"])
    def anthropic_messages() -> Any:
        data = request.get_json()
        if not data:
            return jsonify({"error": {"type": "invalid_request_error", "message": "Request body required"}}), 400

        messages = data.get("messages", [])
        model = data.get("model", "auto")
        max_tokens = data.get("max_tokens", 4096)
        stream = data.get("stream", False)

        # Extract last user message
        content = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                msg_content = msg.get("content", "")
                if isinstance(msg_content, list):
                    # Handle Anthropic's content blocks format
                    for block in msg_content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            content = block.get("text", "")
                            break
                else:
                    content = msg_content
                break

        if not content:
            return jsonify({"error": {"type": "invalid_request_error", "message": "No user message found"}}), 400

        try:
            if stream:
                return _anthropic_stream_response(get_client(), content, model, max_tokens)
            else:
                response = get_client().chat_simple(content, model_id=model)
                return jsonify({
                    "id": f"msg_{uuid.uuid4().hex[:24]}",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": response}],
                    "model": model,
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                })
        except Exception as e:
            return jsonify({"error": {"type": "api_error", "message": str(e)}}), 500

    def _anthropic_stream_response(client: AssistantClient, content: str, model: str, max_tokens: int) -> Response:
        def generate():
            msg_id = f"msg_{uuid.uuid4().hex[:24]}"

            # Message start
            yield f"event: message_start\ndata: {jsonify({'type': 'message_start', 'message': {'id': msg_id, 'type': 'message', 'role': 'assistant', 'model': model, 'usage': {'input_tokens': 0, 'output_tokens': 0}}}).get_data(as_text=True)}\n\n"

            # Content block start
            yield f"event: content_block_start\ndata: {jsonify({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}}).get_data(as_text=True)}\n\n"

            for event in client.chat(content, model_id=model):
                if event.content:
                    yield f"event: content_block_delta\ndata: {jsonify({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': event.content}}).get_data(as_text=True)}\n\n"

            # Content block stop
            yield f"event: content_block_stop\ndata: {jsonify({'type': 'content_block_stop', 'index': 0}).get_data(as_text=True)}\n\n"

            # Message delta
            yield f"event: message_delta\ndata: {jsonify({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 0}}).get_data(as_text=True)}\n\n"

            # Message stop
            yield f"event: message_stop\ndata: {jsonify({'type': 'message_stop'}).get_data(as_text=True)}\n\n"

        return Response(generate(), mimetype="text/event-stream")

    return app


def run_server(config: Optional[Config] = None) -> None:
    app = create_app(config)
    cfg = config or Config()
    app.run(host=cfg.server_host, port=cfg.server_port, debug=False)

"""Local HTTP API server."""

from __future__ import annotations
from typing import Any, Optional
from flask import Flask, request, jsonify
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

    @app.route("/health", methods=["GET"])
    def health() -> Any:
        return jsonify({"status": "ok", "version": "1.0.0"})

    @app.route("/models", methods=["GET"])
    def list_models() -> Any:
        try:
            models = get_client().list_models()
            return jsonify({"models": [{"id": m.model_id, "name": m.model_name, "rate": m.rate_multiplier, "thinking": m.supports_thinking} for m in models]})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/chat", methods=["POST"])
    def chat() -> Any:
        data = request.get_json()
        if not data or "message" not in data:
            return jsonify({"error": "message required"}), 400
        try:
            response = get_client().chat_simple(data["message"], model_id=data.get("model", "auto"))
            return jsonify({"response": response})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/token/status", methods=["GET"])
    def token_status() -> Any:
        c = get_client()
        return jsonify({"authenticated": c.auth.is_authenticated, "has_profile": c.auth._profile_arn is not None})

    return app


def run_server(config: Optional[Config] = None) -> None:
    app = create_app(config)
    cfg = config or Config()
    app.run(host=cfg.server_host, port=cfg.server_port, debug=False)

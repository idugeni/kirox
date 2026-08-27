"""E2E tests."""

from flask import Flask, jsonify, request


def create_mock_app():
    app = Flask(__name__)

    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.route("/", methods=["POST"])
    def handle():
        target = request.headers.get("x-amz-target", "")
        if "ListAvailableModels" in target:
            return jsonify(
                {
                    "models": [
                        {
                            "modelId": "test",
                            "modelName": "Test",
                            "rateMultiplier": 1.0,
                            "tokenLimits": {"maxInputTokens": 100000, "maxOutputTokens": 64000},
                        }
                    ]
                }
            )
        if "InvokeMCP" in target:
            return jsonify(
                {
                    "result": {
                        "tools": [
                            {
                                "name": "t",
                                "description": "d",
                                "inputSchema": {"json": {"type": "object", "properties": {}}},
                            }
                        ]
                    }
                }
            )
        return jsonify({"error": "unknown"}), 400

    return app


def test_mock_health():
    with create_mock_app().test_client() as c:
        assert c.get("/health").get_json()["status"] == "ok"


def test_mock_models():
    with create_mock_app().test_client() as c:
        r = c.post(
            "/",
            headers={"x-amz-target": "KiroControlPlaneBearerService.ListAvailableModels"},
            json={},
        )
        assert len(r.get_json()["models"]) == 1


def test_mock_tools():
    with create_mock_app().test_client() as c:
        r = c.post(
            "/",
            headers={"x-amz-target": "AmazonCodeWhispererStreamingService.InvokeMCP"},
            json={},
        )
        assert len(r.get_json()["result"]["tools"]) == 1

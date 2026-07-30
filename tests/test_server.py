"""Tests for server."""

import pytest
from unittest.mock import Mock, patch
from kirox.service.server import create_app
from kirox.utils.config import Config
from kirox.core.models import ModelInfo, TokenLimits


@pytest.fixture
def client():
    app = create_app(Config(token="test", profile_arn="test"))
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def mock_client():
    with patch("kirox.service.server.AssistantClient") as MockClient:
        instance = MockClient.return_value
        instance.list_models.return_value = [
            ModelInfo(model_id="test-model", model_name="Test", description="Test model",
                     rate_multiplier=1.0, rate_unit="Credit",
                     token_limits=TokenLimits(max_input_tokens=100000, max_output_tokens=64000))
        ]
        instance.chat_simple.return_value="Hello from test"
        instance.auth.is_authenticated = True
        instance.auth._profile_arn = "test-arn"
        yield instance


def test_health(client):
    assert client.get("/health").get_json()["status"] == "ok"


def test_root(client):
    data = client.get("/").get_json()
    assert "openai" in data["endpoints"]
    assert "anthropic" in data["endpoints"]


def test_openai_models(client, mock_client):
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["object"] == "list"
    assert len(data["data"]) == 1


def test_openai_chat(client, mock_client):
    resp = client.post("/v1/chat/completions", json={
        "model": "test-model",
        "messages": [{"role": "user", "content": "Hello"}]
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == "Hello from test"


def test_anthropic_messages(client, mock_client):
    resp = client.post("/v1/messages", json={
        "model": "test-model",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "Hello"}]
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["type"] == "message"
    assert data["content"][0]["text"] == "Hello from test"


def test_anthropic_messages_no_content(client):
    resp = client.post("/v1/messages", json={"messages": []})
    assert resp.status_code == 400


def test_api_chat(client, mock_client):
    resp = client.post("/api/chat", json={"message": "Hello"})
    assert resp.status_code == 200
    assert resp.get_json()["response"] == "Hello from test"


def test_api_chat_no_message(client):
    assert client.post("/api/chat", json={}).status_code == 400

"""Tests for server."""

import pytest
from kirox.service.server import create_app
from kirox.utils.config import Config


@pytest.fixture
def client():
    app = create_app(Config(token="test", profile_arn="test"))
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_health(client):
    assert client.get("/health").get_json()["status"] == "ok"


def test_token_status(client):
    assert "authenticated" in client.get("/token/status").get_json()


def test_chat_no_message(client):
    assert client.post("/chat", json={}).status_code == 400

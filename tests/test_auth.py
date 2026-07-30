"""Tests for auth."""

from kirox.core.auth import AuthManager


def test_headers():
    auth = AuthManager(token="tok", profile_arn="arn")
    h = auth.get_headers()
    assert h["Authorization"] == "Bearer tok"

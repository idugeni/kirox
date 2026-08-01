"""Custom exceptions."""

from __future__ import annotations

from typing import Any


class KiroxError(Exception):
    def __init__(self, message: str, details: Any = None):
        super().__init__(message)
        self.message = message
        self.details = details


class AuthenticationError(KiroxError):
    pass


class APIError(KiroxError):
    def __init__(self, message: str, status: int = 0, response_body: Any = None):
        super().__init__(message)
        self.status = status
        self.response_body = response_body


class StreamError(KiroxError):
    pass

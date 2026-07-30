"""Custom exceptions."""

from __future__ import annotations
from typing import Any


class KuroError(Exception):
    def __init__(self, message: str, details: Any = None):
        super().__init__(message)
        self.message = message
        self.details = details


class AuthenticationError(KuroError):
    pass


class APIError(KuroError):
    def __init__(self, message: str, status: int = 0, response_body: Any = None):
        super().__init__(message)
        self.status = status
        self.response_body = response_body


class StreamError(KuroError):
    pass

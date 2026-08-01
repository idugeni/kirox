"""Kirox — Production-ready SDK for AI coding assistants."""

from kirox._version import __version__
from kirox.core.client import AssistantClient
from kirox.core.errors import APIError, AuthenticationError, KiroxError
from kirox.core.eventstream import EventStreamDecoder, parse_eventstream
from kirox.core.models import ModelInfo, StreamEvent, ToolSpec

__all__ = [
    "__version__",
    "AssistantClient",
    "EventStreamDecoder",
    "parse_eventstream",
    "ModelInfo",
    "StreamEvent",
    "ToolSpec",
    "KiroxError",
    "APIError",
    "AuthenticationError",
]

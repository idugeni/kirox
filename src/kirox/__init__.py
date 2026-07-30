"""Kirox — Production-ready SDK for AI coding assistants."""

from kirox._version import __version__
from kirox.core.client import AssistantClient
from kirox.core.eventstream import parse_eventstream
from kirox.core.models import ModelInfo, StreamEvent, ToolSpec
from kirox.core.errors import KuroError, APIError, AuthenticationError

__version__ = __version__
__all__ = [
    "AssistantClient",
    "parse_eventstream",
    "ModelInfo",
    "StreamEvent",
    "ToolSpec",
    "KuroError",
    "APIError",
    "AuthenticationError",
]

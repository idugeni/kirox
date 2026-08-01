"""Optional MCP stdio server for the synchronous Kirox client."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

from kirox._version import __version__
from kirox.core.auth import AuthManager
from kirox.core.client import AssistantClient
from kirox.utils.config import load_config

MCP_INSTALL_MESSAGE = (
    'MCP support is not installed. Install it with: python -m pip install "kirox[mcp]"'
)
TOOL_NAME = "kirox_chat"
_TOOL_ARGUMENTS = frozenset({"message", "model"})


class MCPDependencyError(RuntimeError):
    """Raised when the optional MCP SDK is needed but unavailable."""


def _load_mcp() -> tuple[Any, Any, Any, Any]:
    """Import the optional MCP SDK only when MCP functionality is requested."""
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import TextContent, Tool
    except ImportError as error:
        raise MCPDependencyError(MCP_INSTALL_MESSAGE) from error
    return Server, stdio_server, TextContent, Tool


def _default_client() -> AssistantClient:
    config = load_config()
    auth = AuthManager.resolve(config=config)
    return AssistantClient(auth=auth, region=config.region)


class _ClientOwner:
    """Lazily create and close exactly one synchronous client."""

    def __init__(
        self,
        client: AssistantClient | None = None,
        *,
        factory: Callable[[], AssistantClient] | None = None,
    ) -> None:
        self._client = client
        self._factory = factory or _default_client
        self._closed = False

    def get(self) -> AssistantClient:
        if self._closed:
            raise RuntimeError("MCP client is closed")
        if self._client is None:
            self._client = self._factory()
        return self._client

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._client is not None:
            self._client.close()


def _validate_call(
    name: str,
    arguments: dict[str, Any] | None,
) -> tuple[str, str]:
    if name != TOOL_NAME:
        raise ValueError(f"Unknown tool: {name}")
    if not isinstance(arguments, dict):
        raise ValueError("kirox_chat arguments must be an object")

    unknown = sorted(set(arguments) - _TOOL_ARGUMENTS)
    if unknown:
        raise ValueError(f"Unsupported kirox_chat argument: {unknown[0]}")

    message = arguments.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("kirox_chat message must be a non-empty string")

    model = arguments.get("model", "auto")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("kirox_chat model must be a non-empty string")
    return message, model.strip()


async def _call_chat(
    owner: _ClientOwner,
    name: str,
    arguments: dict[str, Any] | None,
) -> str:
    message, model = _validate_call(name, arguments)
    client = owner.get()
    return await asyncio.to_thread(client.chat_simple, message, model_id=model)


def _create_server(owner: _ClientOwner, components: tuple[Any, Any, Any, Any]) -> Any:
    Server, _, TextContent, Tool = components

    @asynccontextmanager
    async def lifespan(server: Any) -> AsyncGenerator[dict[str, Any], None]:
        del server
        try:
            yield {}
        finally:
            owner.close()

    server = Server("kirox", version=__version__, lifespan=lifespan)

    @server.list_tools()
    async def list_tools() -> list[Any]:
        return [
            Tool(
                name=TOOL_NAME,
                description="Send a text-only chat request through Kirox",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "minLength": 1},
                        "model": {"type": "string", "minLength": 1, "default": "auto"},
                    },
                    "required": ["message"],
                    "additionalProperties": False,
                },
            )
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[Any]:
        text = await _call_chat(owner, name, arguments)
        return [TextContent(type="text", text=text)]

    return server


def create_server(client: AssistantClient | None = None) -> Any:
    """Create one MCP server that owns one lazy or injected client."""
    return _create_server(_ClientOwner(client), _load_mcp())


async def _run_stdio() -> None:
    components = _load_mcp()
    _, stdio_server, _, _ = components
    owner = _ClientOwner()
    server = _create_server(owner, components)
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        owner.close()


def main() -> int:
    """Run the MCP stdio transport, or explain how to install its extra."""
    try:
        asyncio.run(_run_stdio())
    except MCPDependencyError as error:
        print(error, file=sys.stderr)
        return 1
    return 0

"""MCP server implementation."""

import os, sys
from typing import Any

try:
    from mcp.server import Server
    from mcp.types import TextContent, Tool
except ImportError:
    print("pip install kirox[mcp]", file=sys.stderr); sys.exit(1)

from kirox.core.client import AssistantClient


def create_server():
    server = Server("kirox")
    client = None

    def get_client():
        nonlocal client
        if client is None:
            token = os.environ.get("KURO_TOKEN")
            if token:
                from kirox.core.auth import AuthManager
                client = AssistantClient(auth=AuthManager(token=token))
            else:
                client = AssistantClient.from_cli_db(os.environ.get("ASSISTANT_DB_PATH"))
        return client

    @server.list_tools()
    async def list_tools():
        return [Tool(name="kirox_chat", description="Chat with AI", inputSchema={"type": "object", "properties": {"message": {"type": "string"}, "model": {"type": "string", "default": "auto"}}, "required": ["message"]})]

    @server.call_tool()
    async def call_tool(name, arguments):
        c = get_client()
        if name == "kirox_chat":
            return [TextContent(type="text", text=c.chat_simple(arguments["message"], arguments.get("model", "auto")))]
        return [TextContent(type="text", text=f"Unknown: {name}")]

    return server


def main():
    import asyncio
    from mcp.server.stdio import stdio_server
    async def run():
        async with stdio_server() as (r, w):
            await create_server().run(r, w, create_server().create_initialization_options())
    asyncio.run(run())

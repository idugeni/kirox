"""MCP server tests with no network or real credentials."""

from __future__ import annotations

import asyncio
import builtins
import threading
from collections.abc import AsyncGenerator, Callable, Coroutine
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest

import kirox.mcp.server as mcp_server
from kirox.core.client import AssistantClient


class FakeClient:
    def __init__(self) -> None:
        self.chat_calls: list[tuple[str, str]] = []
        self.chat_thread: int | None = None
        self.close_calls = 0

    def chat_simple(self, message: str, model_id: str = "auto") -> str:
        self.chat_calls.append((message, model_id))
        self.chat_thread = threading.get_ident()
        return "answer"

    def close(self) -> None:
        self.close_calls += 1


def as_client(client: FakeClient) -> AssistantClient:
    return cast(AssistantClient, client)


def test_call_tool_validates_and_runs_sync_client_in_worker_thread() -> None:
    fake = FakeClient()
    owner = mcp_server._ClientOwner(as_client(fake))
    event_loop_thread = threading.get_ident()

    result = asyncio.run(
        mcp_server._call_chat(
            owner,
            "kirox_chat",
            {"message": "hello", "model": " test-model "},
        )
    )

    assert result == "answer"
    assert fake.chat_calls == [("hello", "test-model")]
    assert fake.chat_thread is not None and fake.chat_thread != event_loop_thread


@pytest.mark.parametrize(
    ("name", "arguments", "error"),
    [
        ("unknown", {"message": "hello"}, "Unknown tool"),
        ("kirox_chat", None, "must be an object"),
        ("kirox_chat", {}, "message must be"),
        ("kirox_chat", {"message": " "}, "message must be"),
        ("kirox_chat", {"message": "hello", "model": 7}, "model must be"),
        (
            "kirox_chat",
            {"message": "hello", "temperature": 0},
            "Unsupported kirox_chat argument",
        ),
    ],
)
def test_call_tool_rejects_unknown_or_invalid_input(
    name: str,
    arguments: dict[str, Any] | None,
    error: str,
) -> None:
    factory_calls = 0

    def factory() -> AssistantClient:
        nonlocal factory_calls
        factory_calls += 1
        return as_client(FakeClient())

    owner = mcp_server._ClientOwner(factory=factory)

    with pytest.raises(ValueError, match=error):
        asyncio.run(mcp_server._call_chat(owner, name, arguments))

    assert factory_calls == 0


def test_client_owner_is_lazy_single_and_closes_once() -> None:
    fake = FakeClient()
    factory_calls = 0

    def factory() -> AssistantClient:
        nonlocal factory_calls
        factory_calls += 1
        return as_client(fake)

    owner = mcp_server._ClientOwner(factory=factory)

    assert owner.get() is owner.get()
    owner.close()
    owner.close()

    assert factory_calls == 1
    assert fake.close_calls == 1
    with pytest.raises(RuntimeError, match="closed"):
        owner.get()


class FakeValue(SimpleNamespace):
    def __init__(self, **values: Any) -> None:
        super().__init__(**values)


class FakeServer:
    instances: list[FakeServer] = []

    def __init__(
        self,
        name: str,
        *,
        version: str,
        lifespan: Callable[[Any], Any],
    ) -> None:
        self.name = name
        self.version = version
        self.lifespan = lifespan
        self.list_handler: Callable[[], Coroutine[Any, Any, list[Any]]] | None = None
        self.call_handler: Callable[..., Coroutine[Any, Any, list[Any]]] | None = None
        self.run_calls = 0
        self.result: list[Any] = []
        FakeServer.instances.append(self)

    def list_tools(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(handler: Callable[..., Any]) -> Callable[..., Any]:
            self.list_handler = handler
            return handler

        return decorator

    def call_tool(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(handler: Callable[..., Any]) -> Callable[..., Any]:
            self.call_handler = handler
            return handler

        return decorator

    def create_initialization_options(self) -> object:
        return object()

    async def run(self, read_stream: object, write_stream: object, options: object) -> None:
        del read_stream, write_stream, options
        self.run_calls += 1
        assert self.list_handler is not None
        assert self.call_handler is not None
        async with self.lifespan(self):
            tools = await self.list_handler()
            assert tools[0].inputSchema["additionalProperties"] is False
            self.result = await self.call_handler("kirox_chat", {"message": "hello"})


@asynccontextmanager
async def fake_stdio() -> AsyncGenerator[tuple[object, object], None]:
    yield object(), object()


def test_stdio_runtime_uses_one_server_and_client_then_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeClient()
    FakeServer.instances.clear()
    components = (FakeServer, fake_stdio, FakeValue, FakeValue)
    monkeypatch.setattr(mcp_server, "_load_mcp", lambda: components)
    monkeypatch.setattr(mcp_server, "_default_client", lambda: as_client(fake))

    asyncio.run(mcp_server._run_stdio())

    assert len(FakeServer.instances) == 1
    server = FakeServer.instances[0]
    assert server.name == "kirox"
    assert server.version == "1.1.0"
    assert server.run_calls == 1
    assert server.result[0].text == "answer"
    assert fake.chat_calls == [("hello", "auto")]
    assert fake.close_calls == 1


def test_actual_mcp_sdk_registers_tool_and_closes_injected_client() -> None:
    pytest.importorskip("mcp")
    fake = FakeClient()
    server = mcp_server.create_server(as_client(fake))
    options = server.create_initialization_options()

    assert options.server_name == "kirox"
    assert options.server_version == "1.1.0"
    assert options.capabilities.tools is not None

    async def enter_lifespan() -> None:
        async with server.lifespan(server):
            pass

    asyncio.run(enter_lifespan())
    assert fake.close_calls == 1


def test_lazy_import_failure_has_clear_console_message(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original_import = builtins.__import__

    def blocked_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError("blocked for test")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    with pytest.raises(mcp_server.MCPDependencyError, match="kirox\\[mcp\\]"):
        mcp_server._load_mcp()
    assert mcp_server.main() == 1

    error = capsys.readouterr().err
    assert "MCP support is not installed" in error
    assert 'python -m pip install "kirox[mcp]"' in error

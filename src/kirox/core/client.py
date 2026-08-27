"""Main API client."""

from __future__ import annotations

import uuid
from copy import deepcopy
from types import TracebackType
from typing import Any, Generator, Optional

import httpx

from kirox.core.auth import AuthManager
from kirox.core.errors import APIError, AuthenticationError, StreamError
from kirox.core.eventstream import EventStreamDecoder
from kirox.core.models import ModelInfo, StreamEvent, ToolSpec


def _optional_model_id(event_data: dict[str, Any]) -> Optional[str]:
    """Read `modelId` only when upstream sends it as a string."""
    model_id = event_data.get("modelId")
    return model_id if isinstance(model_id, str) else None


def _is_complete_tool_payload(tool: dict[str, Any]) -> bool:
    """Report whether an upstream tool entry carries a usable name and schema."""
    specification = tool.get("toolSpecification", tool)
    if not isinstance(specification, dict):
        return False
    name = specification.get("name")
    if not isinstance(name, str) or not name.strip():
        return False
    return isinstance(specification.get("inputSchema"), dict)


class AssistantClient:
    def __init__(
        self,
        auth: Optional[AuthManager] = None,
        runtime_url: Optional[str] = None,
        region: str = "us-east-1",
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self._auth = auth
        self._region = region
        self._runtime_url = runtime_url or f"https://runtime.{region}.kiro.dev"
        self._management_url = f"https://management.{region}.kiro.dev"
        self._session_id: Optional[str] = None
        self._conversation_id: Optional[str] = None
        self._http = http_client

    @classmethod
    def auto(
        cls,
        region: str = "us-east-1",
        *,
        http_client: Optional[httpx.Client] = None,
    ) -> AssistantClient:
        """Auto-detect credentials from deterministic supported sources."""
        return cls(auth=AuthManager.auto_detect(), region=region, http_client=http_client)

    @classmethod
    def from_cli_db(
        cls,
        db_path: Optional[str] = None,
        region: str = "us-east-1",
        *,
        http_client: Optional[httpx.Client] = None,
    ) -> AssistantClient:
        return cls(
            auth=AuthManager.from_cli_db(db_path),
            region=region,
            http_client=http_client,
        )

    @classmethod
    def from_env(
        cls,
        region: str = "us-east-1",
        *,
        http_client: Optional[httpx.Client] = None,
    ) -> AssistantClient:
        return cls(auth=AuthManager.from_env(), region=region, http_client=http_client)

    @property
    def auth(self) -> AuthManager:
        if self._auth is None:
            self._auth = AuthManager.auto_detect()
        return self._auth

    @property
    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=120)
        return self._http

    def replace_auth(self, auth: AuthManager) -> None:
        """Replace credentials used by subsequent requests."""
        self._auth = auth

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def __enter__(self) -> AssistantClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def list_models(self) -> list[ModelInfo]:
        response = self._client.post(
            self._management_url,
            headers={
                **self.auth.get_headers(),
                "x-amz-target": "KiroControlPlaneBearerService.ListAvailableModels",
            },
            json={"origin": "AI_EDITOR", "profileArn": self.auth.profile_arn},
        )
        try:
            if response.status_code != 200:
                raise APIError(f"Error {response.status_code}", response.status_code)
            try:
                payload: Any = response.json()
            except ValueError as exc:
                raise APIError("Invalid models response", 200, response.text) from exc
            if not isinstance(payload, dict):
                raise APIError("Invalid models response", 200, payload)
            models = payload.get("models", [])
            if not isinstance(models, list) or not all(isinstance(model, dict) for model in models):
                raise APIError("Invalid models response models", 200, payload)
            try:
                return [ModelInfo.from_api(model) for model in models]
            except ValueError as exc:
                raise APIError("Invalid model specification", 200, payload) from exc
        finally:
            response.close()

    def list_tools(self) -> list[ToolSpec]:
        body = {
            "id": "tools_list",
            "method": "tools/list",
            "profileArn": self.auth.profile_arn,
            "jsonrpc": "2.0",
            "params": {"includeHidden": True},
        }
        response = self._client.post(
            self._runtime_url,
            headers={
                **self.auth.get_headers(),
                "x-amz-target": "KiroRuntimeService.InvokeMCP",
            },
            json=body,
        )
        try:
            if response.status_code != 200:
                try:
                    response_body: Any = response.json()
                except ValueError:
                    response_body = response.text
                raise APIError(
                    f"Error {response.status_code}",
                    response.status_code,
                    response_body,
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise APIError("Invalid tools response", 200, response.text) from exc

            if not isinstance(payload, dict):
                raise APIError("Invalid tools response", 200, payload)
            if "error" in payload:
                raise APIError("MCP tools/list error", 200, payload)

            result = payload.get("result")
            if not isinstance(result, dict):
                raise APIError("Invalid tools response result", 200, payload)
            tools = result.get("tools")
            if not isinstance(tools, list) or not all(isinstance(tool, dict) for tool in tools):
                raise APIError("Invalid tools response tools", 200, payload)
            if not all(_is_complete_tool_payload(tool) for tool in tools):
                raise APIError("Incomplete tool specification", 200, payload)

            try:
                return [ToolSpec.from_api(tool) for tool in tools]
            except ValueError as exc:
                raise APIError("Invalid tool specification", 200, payload) from exc
        finally:
            response.close()

    def chat(
        self,
        message: str,
        *,
        model_id: str = "auto",
        tools: Optional[list[Any]] = None,
    ) -> Generator[StreamEvent, None, None]:
        auth = self.auth
        if not auth.is_authenticated:
            raise AuthenticationError("Not authenticated")

        normalized_tools = [
            tool.to_api() if isinstance(tool, ToolSpec) else deepcopy(dict(tool))
            for tool in (tools or [])
        ]
        conversation_id = f"conv_{uuid.uuid4()}"
        body = {
            "profileArn": auth.profile_arn,
            "conversationState": {
                "currentMessage": {
                    "userInputMessage": {
                        "content": message,
                        "userInputMessageContext": {"tools": normalized_tools},
                        "modelId": model_id,
                    }
                },
                "chatTriggerType": "MANUAL",
                "conversationId": conversation_id,
            },
        }
        decoder = EventStreamDecoder()
        terminal = False

        with self._client.stream(
            "POST",
            self._runtime_url,
            headers={
                **auth.get_headers(),
                "x-amz-target": "KiroRuntimeService.GenerateAssistantResponse",
            },
            json=body,
        ) as response:
            if response.status_code != 200:
                raise APIError(f"Error {response.status_code}", response.status_code)

            for chunk in response.iter_bytes():
                for message_frame in decoder.feed(chunk):
                    event_data = message_frame.body_object()
                    if message_frame.event_type != "assistantResponseEvent":
                        yield StreamEvent(
                            event_type=message_frame.event_type,
                            raw=deepcopy(event_data),
                        )
                        continue
                    if event_data.get("content") is None:
                        decoder.finalize()
                        terminal = True
                        break
                    content = event_data["content"]
                    if not isinstance(content, str):
                        raise StreamError("EventStream assistant content must be a string")
                    yield StreamEvent(
                        event_type="content",
                        content=content,
                        model_id=_optional_model_id(event_data),
                    )
                if terminal:
                    break
            if not terminal:
                decoder.finalize()

        yield StreamEvent(event_type="end", done=True)

    def chat_simple(self, message: str, model_id: str = "auto") -> str:
        return "".join(
            event.content for event in self.chat(message, model_id=model_id) if event.content
        )

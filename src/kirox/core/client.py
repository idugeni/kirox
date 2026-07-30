"""Main API client."""

from __future__ import annotations
import uuid
from typing import Any, Generator, Optional
import httpx
from kirox.core.auth import AuthManager
from kirox.core.errors import APIError, AuthenticationError
from kirox.core.eventstream import parse_eventstream
from kirox.core.models import ModelInfo, StreamEvent, ToolSpec


class AssistantClient:
    def __init__(self, auth: Optional[AuthManager] = None, runtime_url: Optional[str] = None, region: str = "us-east-1"):
        self._auth = auth
        self._region = region
        self._runtime_url = runtime_url or f"https://runtime.{region}.kiro.dev"
        self._management_url = f"https://management.{region}.kiro.dev"
        self._session_id: Optional[str] = None
        self._conversation_id: Optional[str] = None
        self._http: Optional[httpx.Client] = None

    @classmethod
    def from_cli_db(cls, db_path: Optional[str] = None, region: str = "us-east-1") -> AssistantClient:
        return cls(auth=AuthManager.from_cli_db(db_path), region=region)

    @classmethod
    def from_env(cls, region: str = "us-east-1") -> AssistantClient:
        return cls(auth=AuthManager.from_env(), region=region)

    @property
    def auth(self) -> AuthManager:
        if self._auth is None: self._auth = AuthManager.from_env()
        return self._auth

    @property
    def _client(self) -> httpx.Client:
        if self._http is None: self._http = httpx.Client(timeout=120)
        return self._http

    def close(self) -> None:
        if self._http: self._http.close(); self._http = None

    def __enter__(self): return self
    def __exit__(self, *args): self.close()

    def list_models(self) -> list[ModelInfo]:
        resp = self._client.post(self._management_url, headers={**self.auth.get_headers(), "x-amz-target": "KiroControlPlaneBearerService.ListAvailableModels"}, json={"origin": "AI_EDITOR", "profileArn": self.auth._profile_arn})
        if resp.status_code != 200: raise APIError(f"Error {resp.status_code}", resp.status_code)
        return [ModelInfo.from_api(m) for m in resp.json().get("models", [])]

    def list_tools(self) -> list[ToolSpec]:
        body = {"id": "tools_list", "method": "tools/list", "profileArn": self.auth._profile_arn, "jsonrpc": "2.0", "params": {"includeHidden": True}}
        resp = self._client.post(self._runtime_url, headers={**self.auth.get_headers(), "x-amz-target": "KiroRuntimeService.InvokeMCP"}, json=body)
        if resp.status_code != 200: raise APIError(f"Error {resp.status_code}", resp.status_code)
        return [ToolSpec.from_api(t) for t in resp.json().get("result", {}).get("tools", [])]

    def chat(self, message: str, *, model_id: str = "auto", tools: Optional[list[dict[str, Any]]] = None) -> Generator[StreamEvent, None, None]:
        if not self.auth.is_authenticated: raise AuthenticationError("Not authenticated")
        if not self._session_id: self._session_id = f"sess_{uuid.uuid4()}"
        self._conversation_id = self._session_id
        body = {"profileArn": self.auth._profile_arn, "conversationState": {"currentMessage": {"userInputMessage": {"content": message, "userInputMessageContext": {"tools": tools or []}, "modelId": model_id}}, "chatTriggerType": "MANUAL", "conversationId": self._conversation_id}}
        with self._client.stream("POST", self._runtime_url, headers={**self.auth.get_headers(), "x-amz-target": "KiroRuntimeService.GenerateAssistantResponse"}, json=body) as resp:
            if resp.status_code != 200: raise APIError(f"Error {resp.status_code}", resp.status_code)
            for msg in parse_eventstream(resp.read()):
                if msg.event_type == "assistantResponseEvent":
                    try:
                        data = msg.body_json()
                        if data.get("content") is None: yield StreamEvent(event_type="end", done=True); return
                        yield StreamEvent(event_type="content", content=data["content"], model_id=data.get("modelId"))
                    except Exception: pass
        yield StreamEvent(event_type="end", done=True)

    def chat_simple(self, message: str, model_id: str = "auto") -> str:
        return "".join(e.content for e in self.chat(message, model_id=model_id) if e.content)

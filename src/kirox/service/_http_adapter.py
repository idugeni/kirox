"""Internal normalization for text-only provider-compatible HTTP requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

MAX_TOKENS_WARNING: Final = "max_tokens is accepted for compatibility but is not enforced by Kirox"

_ALLOWED_ROLES: Final = frozenset({"system", "user", "assistant"})
_OPENAI_FIELDS: Final = frozenset({"model", "messages", "stream", "max_tokens"})
_ANTHROPIC_FIELDS: Final = frozenset({"model", "messages", "stream", "max_tokens", "system"})
_MESSAGE_FIELDS: Final = frozenset({"role", "content"})
_TEXT_BLOCK_FIELDS: Final = frozenset({"type", "text"})


class RequestValidationError(ValueError):
    """A deterministic, field-addressable request validation failure."""

    def __init__(self, field: str, message: str, code: str = "invalid_value") -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message
        self.code = code


@dataclass(frozen=True)
class TextChatRequest:
    """Provider-neutral request passed to the single-text upstream client."""

    model: str
    transcript: str
    stream: bool
    max_tokens: int | None
    warnings: tuple[str, ...]


def parse_openai_request(payload: Any) -> TextChatRequest:
    """Validate and normalize an OpenAI chat-completions request."""
    return _parse_request(payload, allowed_fields=_OPENAI_FIELDS)


def parse_anthropic_request(payload: Any) -> TextChatRequest:
    """Validate and normalize an Anthropic messages request."""
    data = _require_object(payload)
    _reject_unknown_fields(data, _ANTHROPIC_FIELDS, "")

    system_messages: list[tuple[str, str]] = []
    if "system" in data:
        system_messages.append(("system", _parse_text_content(data["system"], "system")))
    return _parse_validated_request(data, system_messages)


def _parse_request(payload: Any, *, allowed_fields: frozenset[str]) -> TextChatRequest:
    data = _require_object(payload)
    _reject_unknown_fields(data, allowed_fields, "")
    return _parse_validated_request(data, [])


def _require_object(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RequestValidationError("body", "must be a valid JSON object", "invalid_json")
    return payload


def _parse_validated_request(
    data: dict[str, Any], leading_messages: list[tuple[str, str]]
) -> TextChatRequest:
    model = data.get("model")
    if model is None:
        raise RequestValidationError("model", "is required", "missing_required_parameter")
    if not isinstance(model, str) or not model.strip():
        raise RequestValidationError("model", "must be a non-empty string")

    if "messages" not in data:
        raise RequestValidationError("messages", "is required", "missing_required_parameter")
    raw_messages = data["messages"]
    if not isinstance(raw_messages, list) or not raw_messages:
        raise RequestValidationError("messages", "must be a non-empty array")

    messages = [*leading_messages]
    for index, raw_message in enumerate(raw_messages):
        field = f"messages[{index}]"
        if not isinstance(raw_message, dict):
            raise RequestValidationError(field, "must be an object")
        _reject_unknown_fields(raw_message, _MESSAGE_FIELDS, f"{field}.")

        role = raw_message.get("role")
        if not isinstance(role, str) or role not in _ALLOWED_ROLES:
            raise RequestValidationError(
                f"{field}.role",
                "must be one of system, user, or assistant",
                "unsupported_value",
            )
        if "content" not in raw_message:
            raise RequestValidationError(
                f"{field}.content", "is required", "missing_required_parameter"
            )
        content = _parse_text_content(raw_message["content"], f"{field}.content")
        messages.append((role, content))

    if messages[-1][0] != "user":
        raise RequestValidationError(
            f"messages[{len(raw_messages) - 1}].role",
            "final message must have role user",
            "invalid_message_order",
        )

    stream = data.get("stream", False)
    if not isinstance(stream, bool):
        raise RequestValidationError("stream", "must be a boolean")

    max_tokens: int | None = None
    warnings: tuple[str, ...] = ()
    if "max_tokens" in data:
        max_tokens = data["max_tokens"]
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
            raise RequestValidationError("max_tokens", "must be a positive integer")
        warnings = (MAX_TOKENS_WARNING,)

    transcript = "\n\n".join(f"{role.upper()}:\n{content}" for role, content in messages)
    return TextChatRequest(
        model=model.strip(),
        transcript=transcript,
        stream=stream,
        max_tokens=max_tokens,
        warnings=warnings,
    )


def _parse_text_content(value: Any, field: str) -> str:
    if isinstance(value, str):
        if not value.strip():
            raise RequestValidationError(field, "must contain non-whitespace text")
        return value

    if not isinstance(value, list) or not value:
        raise RequestValidationError(field, "must be text or a non-empty array of text blocks")

    parts: list[str] = []
    for index, raw_block in enumerate(value):
        block_field = f"{field}[{index}]"
        if not isinstance(raw_block, dict):
            raise RequestValidationError(block_field, "must be an object")
        block_type = raw_block.get("type")
        if block_type != "text":
            raise RequestValidationError(
                f"{block_field}.type",
                "only text content blocks are supported",
                "unsupported_content_type",
            )
        _reject_unknown_fields(raw_block, _TEXT_BLOCK_FIELDS, f"{block_field}.")
        text = raw_block.get("text")
        if not isinstance(text, str) or not text.strip():
            raise RequestValidationError(f"{block_field}.text", "must be non-empty text")
        parts.append(text)
    return "".join(parts)


def _reject_unknown_fields(
    value: dict[str, Any], allowed_fields: frozenset[str], prefix: str
) -> None:
    unknown_fields = sorted(set(value) - allowed_fields)
    if unknown_fields:
        field = f"{prefix}{unknown_fields[0]}"
        raise RequestValidationError(
            field,
            "is not supported by the Kirox text-only adapter",
            "unsupported_parameter",
        )

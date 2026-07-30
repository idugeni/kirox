"""Data models."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class TokenLimits:
    max_input_tokens: int
    max_output_tokens: int


@dataclass(frozen=True)
class ModelInfo:
    model_id: str
    model_name: str
    description: str
    rate_multiplier: float
    rate_unit: str
    token_limits: TokenLimits
    supports_thinking: bool = False

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> ModelInfo:
        limits = data.get("tokenLimits", {})
        return cls(
            model_id=data["modelId"],
            model_name=data.get("modelName", data["modelId"]),
            description=data.get("description", ""),
            rate_multiplier=data.get("rateMultiplier", 1.0),
            rate_unit=data.get("rateUnit", "Credit"),
            token_limits=TokenLimits(
                max_input_tokens=limits.get("maxInputTokens", 200000),
                max_output_tokens=limits.get("maxOutputTokens", 64000),
            ),
            supports_thinking=data.get("additionalModelRequestFieldsSchema") is not None,
        )


@dataclass(frozen=True)
class ToolParam:
    name: str
    type: str
    description: str = ""
    required: bool = False


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: tuple[ToolParam, ...] = ()

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> ToolSpec:
        spec = data.get("toolSpecification", data)
        schema = spec.get("inputSchema", {}).get("json", {})
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        params = tuple(
            ToolParam(name=k, type=v.get("type", "string"), description=v.get("description", ""), required=k in required)
            for k, v in props.items()
        )
        return cls(name=spec.get("name", ""), description=spec.get("description", ""), parameters=params)


@dataclass
class StreamEvent:
    event_type: str
    content: Optional[str] = None
    model_id: Optional[str] = None
    done: bool = False
    raw: Optional[dict[str, Any]] = None

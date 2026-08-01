"""Data models."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Optional


def _optional_str(data: dict[str, Any], key: str, default: str) -> str:
    """Read a string field, falling back when upstream omits it or sends null."""
    value = data.get(key)
    return value if isinstance(value, str) else default


def _optional_int(data: dict[str, Any], key: str, default: int) -> int:
    """Read an integer field, rejecting bools, floats, null, and other shapes."""
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _optional_float(data: dict[str, Any], key: str, default: float) -> float:
    """Read a numeric field, rejecting bools, null, and other shapes."""
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


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
        model_id = data.get("modelId")
        if not isinstance(model_id, str) or not model_id:
            raise ValueError("Model modelId must be a non-empty string")
        limits = data.get("tokenLimits")
        if not isinstance(limits, dict):
            limits = {}
        return cls(
            model_id=model_id,
            model_name=_optional_str(data, "modelName", model_id),
            description=_optional_str(data, "description", ""),
            rate_multiplier=_optional_float(data, "rateMultiplier", 1.0),
            rate_unit=_optional_str(data, "rateUnit", "Credit"),
            token_limits=TokenLimits(
                max_input_tokens=_optional_int(limits, "maxInputTokens", 200000),
                max_output_tokens=_optional_int(limits, "maxOutputTokens", 64000),
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
    input_schema: dict[str, Any] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_schema", deepcopy(self.input_schema))

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> ToolSpec:
        spec = data.get("toolSpecification", data)
        if not isinstance(spec, dict):
            raise ValueError("Tool specification must be an object")
        input_schema = spec.get("inputSchema", {})
        if not isinstance(input_schema, dict):
            raise ValueError("Tool inputSchema must be an object")
        schema = input_schema.get("json", {})
        if not isinstance(schema, dict):
            raise ValueError("Tool inputSchema.json must be an object")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError("Tool schema properties must be an object")
        required_values = schema.get("required", [])
        if not isinstance(required_values, list) or not all(
            isinstance(value, str) for value in required_values
        ):
            raise ValueError("Tool schema required must be an array of strings")
        required = set(required_values)

        params = []
        for name, value in properties.items():
            if not isinstance(name, str) or not isinstance(value, dict):
                raise ValueError("Tool properties must contain object schemas")
            param_type = value.get("type", "string")
            description = value.get("description", "")
            params.append(
                ToolParam(
                    name=name,
                    type=param_type if isinstance(param_type, str) else "string",
                    description=description if isinstance(description, str) else "",
                    required=name in required,
                )
            )
        name = spec.get("name", "")
        description = spec.get("description", "")
        return cls(
            name=name if isinstance(name, str) else "",
            description=description if isinstance(description, str) else "",
            parameters=tuple(params),
            input_schema=schema,
        )

    def to_api(self) -> dict[str, Any]:
        schema: dict[str, Any] = deepcopy(self.input_schema)
        if not schema:
            properties: dict[str, dict[str, Any]] = {}
            required: list[str] = []
            for parameter in self.parameters:
                property_schema = {"type": parameter.type}
                if parameter.description:
                    property_schema["description"] = parameter.description
                properties[parameter.name] = property_schema
                if parameter.required:
                    required.append(parameter.name)
            schema = {"type": "object", "properties": properties}
            if required:
                schema["required"] = required

        return {
            "toolSpecification": {
                "name": self.name,
                "description": self.description,
                "inputSchema": {"json": schema},
            }
        }


@dataclass
class StreamEvent:
    event_type: str
    content: Optional[str] = None
    model_id: Optional[str] = None
    done: bool = False
    raw: Optional[dict[str, Any]] = None

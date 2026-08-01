"""Tests for models."""

from copy import deepcopy
from typing import Any

import pytest

from kirox.core.models import ModelInfo, TokenLimits, ToolParam, ToolSpec


def test_model_from_api():
    m = ModelInfo.from_api(
        {
            "modelId": "x",
            "modelName": "X",
            "rateMultiplier": 2.0,
            "tokenLimits": {"maxInputTokens": 1000, "maxOutputTokens": 500},
        }
    )
    assert m.model_id == "x" and m.rate_multiplier == 2.0


def test_model_from_api_replaces_null_fields_with_declared_defaults() -> None:
    m = ModelInfo.from_api(
        {
            "modelId": "x",
            "modelName": None,
            "description": None,
            "rateMultiplier": None,
            "rateUnit": None,
            "tokenLimits": None,
        }
    )

    assert m.model_name == "x"
    assert m.description == ""
    assert m.rate_multiplier == 1.0
    assert m.rate_unit == "Credit"
    assert m.token_limits == TokenLimits(200000, 64000)
    assert m.supports_thinking is False


@pytest.mark.parametrize(
    "limits",
    [
        None,
        "not-an-object",
        {"maxInputTokens": None, "maxOutputTokens": "500"},
        {"maxInputTokens": True, "maxOutputTokens": 1.5},
    ],
)
def test_model_from_api_falls_back_on_unusable_token_limits(limits: Any) -> None:
    m = ModelInfo.from_api({"modelId": "x", "tokenLimits": limits})

    assert m.token_limits == TokenLimits(200000, 64000)


@pytest.mark.parametrize("rate", [None, True, "2.0", [2.0]])
def test_model_from_api_falls_back_on_unusable_rate_multiplier(rate: Any) -> None:
    assert ModelInfo.from_api({"modelId": "x", "rateMultiplier": rate}).rate_multiplier == 1.0


def test_model_from_api_widens_integer_rate_multiplier_to_float() -> None:
    rate_multiplier = ModelInfo.from_api({"modelId": "x", "rateMultiplier": 3}).rate_multiplier

    assert rate_multiplier == 3.0
    assert isinstance(rate_multiplier, float)


@pytest.mark.parametrize("data", [{}, {"modelId": None}, {"modelId": ""}, {"modelId": 1}])
def test_model_from_api_rejects_unusable_model_id(data: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="modelId"):
        ModelInfo.from_api(data)


@pytest.mark.parametrize("wrapped", [False, True])
def test_tool_from_api_preserves_schema_and_round_trips_without_aliases(wrapped: bool) -> None:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "q": {
                "type": "string",
                "description": "Query",
                "enum": ["one", "two"],
            },
            "options": {
                "type": "object",
                "additionalProperties": {"type": "integer"},
            },
        },
        "required": ["q"],
        "additionalProperties": False,
        "$defs": {"unused": {"type": "boolean"}},
    }
    specification = {
        "name": "search",
        "description": "Search things",
        "inputSchema": {"json": schema},
    }
    payload = {"toolSpecification": specification} if wrapped else specification
    expected_schema = deepcopy(schema)

    tool = ToolSpec.from_api(payload)

    assert tool.input_schema == expected_schema
    assert tool.parameters == (
        ToolParam(name="q", type="string", description="Query", required=True),
        ToolParam(name="options", type="object"),
    )

    schema["properties"]["q"]["enum"].append("mutated")
    assert tool.input_schema == expected_schema

    serialized: dict[str, Any] = tool.to_api()
    assert serialized == {
        "toolSpecification": {
            "name": "search",
            "description": "Search things",
            "inputSchema": {"json": expected_schema},
        }
    }
    serialized["toolSpecification"]["inputSchema"]["json"]["properties"]["q"]["enum"].append(
        "output mutation"
    )
    assert tool.input_schema == expected_schema
    assert ToolSpec.from_api(tool.to_api()).to_api() == tool.to_api()


def test_tool_legacy_constructor_builds_simple_api_schema() -> None:
    tool = ToolSpec(
        "legacy",
        "Legacy tool",
        (ToolParam("path", "string", "File path", required=True),),
    )

    assert tool.to_api() == {
        "toolSpecification": {
            "name": "legacy",
            "description": "Legacy tool",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "File path"}},
                    "required": ["path"],
                }
            },
        }
    }

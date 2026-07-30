"""Tests for models."""

from kirox.core.models import ModelInfo, ToolSpec


def test_model_from_api():
    m = ModelInfo.from_api({"modelId": "x", "modelName": "X", "rateMultiplier": 2.0, "tokenLimits": {"maxInputTokens": 1000, "maxOutputTokens": 500}})
    assert m.model_id == "x" and m.rate_multiplier == 2.0


def test_tool_from_api():
    t = ToolSpec.from_api({"toolSpecification": {"name": "t", "inputSchema": {"json": {"properties": {"q": {"type": "string"}}, "required": ["q"]}}}})
    assert t.name == "t" and t.parameters[0].required is True

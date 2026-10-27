"""Tests for Tool model."""

import pytest

from nova.tools.tool import RiskLevel, Tool, ToolResult


def test_risk_levels():
    assert RiskLevel.READ.value == "read"
    assert RiskLevel.WRITE.value == "write"
    assert RiskLevel.DESTRUCTIVE.value == "destructive"


def test_tool_creation():
    def impl():
        return 42

    tool = Tool(
        name="test.tool",
        description="A test tool",
        implementation=impl,
    )
    assert tool.name == "test.tool"
    assert tool.risk == RiskLevel.READ
    assert tool.permission == ""
    assert tool.parameters == {}


def test_tool_default_risk_is_read():
    def impl():
        return 42

    tool = Tool(name="t", description="d", implementation=impl)
    assert tool.risk == RiskLevel.READ


def test_tool_with_parameters():
    def impl(x: int):
        return x

    tool = Tool(
        name="test.add",
        description="Add one",
        parameters={
            "type": "object",
            "properties": {"x": {"type": "integer"}},
        },
        implementation=impl,
    )
    assert tool.parameters["properties"]["x"]["type"] == "integer"


def test_tool_result_success():
    result = ToolResult(success=True, data=[1, 2, 3])
    assert result.success is True
    assert result.data == [1, 2, 3]
    assert result.error is None


def test_tool_result_error():
    result = ToolResult(success=False, error="boom")
    assert result.success is False
    assert result.error == "boom"
    assert result.data is None


@pytest.mark.asyncio
async def test_async_tool_implementation():
    async def impl():
        return "async result"

    tool = Tool(name="test.async", description="Async tool", implementation=impl)
    result = await tool.implementation()
    assert result == "async result"
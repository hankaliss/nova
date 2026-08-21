"""Tests for Agent tool calling (mocked LLM + real registry)."""

import json
from unittest.mock import AsyncMock

import pytest

from nova.agent.agent import Agent
from nova.llm.models import FunctionCall, Role, StreamChunk, ToolCall
from nova.tools.registry import ToolRegistry


def make_tool_calling_provider():
    """First call returns a tool call, second call returns the final answer."""

    call_count = 0

    async def mock_stream(messages, model="", tools=None, options=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield StreamChunk(
                token='{"name": "linux.uptime", "arguments": {}}',
                done=True,
                tool_calls=[
                    ToolCall(
                        function=FunctionCall(
                            name="linux.uptime", arguments={}
                        )
                    )
                ],
            )
        else:
            yield StreamChunk(token="The system has been up for a while.", done=False)
            yield StreamChunk(token="", done=True, eval_count=10)

    provider = AsyncMock()
    provider.chat_stream = mock_stream
    return provider, lambda: call_count


@pytest.mark.asyncio
async def test_agent_calls_tool_and_finishes():
    provider, calls = make_tool_calling_provider()
    registry = ToolRegistry()
    registry.register_linux_tools()

    agent = Agent(provider=provider, model="test-model", registry=registry)
    response = await agent.send_sync("How long has the system been up?")

    assert calls() == 2
    assert response == "The system has been up for a while."


@pytest.mark.asyncio
async def test_agent_context_contains_tool_messages():
    provider, _ = make_tool_calling_provider()
    registry = ToolRegistry()
    registry.register_linux_tools()

    agent = Agent(provider=provider, model="test-model", registry=registry)
    await agent.send_sync("Check uptime")

    roles = [m.role for m in agent.context.messages]
    assert Role.USER in roles
    assert Role.ASSISTANT in roles
    assert Role.TOOL in roles


@pytest.mark.asyncio
async def test_agent_tool_result_contains_uptime_data():
    provider, _ = make_tool_calling_provider()
    registry = ToolRegistry()
    registry.register_linux_tools()

    agent = Agent(provider=provider, model="test-model", registry=registry)
    await agent.send_sync("Check uptime")

    tool_msg = next(m for m in agent.context.messages if m.role == Role.TOOL)
    parsed = json.loads(tool_msg.content)
    assert parsed["success"] is True
    assert "seconds" in parsed["data"]


@pytest.mark.asyncio
async def test_agent_failed_tool_result_is_reported():
    """If the tool fails, the error is passed back to the model."""

    call_count = 0

    async def mock_stream(messages, model="", tools=None, options=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield StreamChunk(
                token="",
                done=True,
                tool_calls=[
                    ToolCall(function=FunctionCall(name="missing.tool", arguments={}))
                ],
            )
        else:
            yield StreamChunk(token="final answer", done=False)
            yield StreamChunk(token="", done=True)

    provider = AsyncMock()
    provider.chat_stream = mock_stream
    registry = ToolRegistry()

    agent = Agent(provider=provider, model="test-model", registry=registry)
    response = await agent.send_sync("do it")
    assert response == "final answer"

    tool_msg = next(m for m in agent.context.messages if m.role == Role.TOOL)
    assert "Unknown tool" in tool_msg.content


@pytest.mark.asyncio
async def test_agent_stops_after_max_tool_iterations():
    """A model that always requests tools is stopped by the iteration guard."""

    async def mock_stream(messages, model="", tools=None, options=None):
        yield StreamChunk(
            token="",
            done=True,
            tool_calls=[
                ToolCall(function=FunctionCall(name="missing.tool", arguments={}))
            ],
        )

    provider = AsyncMock()
    provider.chat_stream = mock_stream
    registry = ToolRegistry()

    agent = Agent(
        provider=provider,
        model="test-model",
        registry=registry,
        max_tool_iterations=3,
    )
    response = await agent.send_sync("do it")
    assert "limit exceeded" in response


@pytest.mark.asyncio
async def test_send_streaming_with_tool_call():
    provider, calls = make_tool_calling_provider()
    registry = ToolRegistry()
    registry.register_linux_tools()

    agent = Agent(provider=provider, model="test-model", registry=registry)
    tokens = []
    async for chunk in agent.send("uptime please"):
        if chunk.token:
            tokens.append(chunk.token)

    assert calls() == 2
    assert "".join(tokens) == "The system has been up for a while."
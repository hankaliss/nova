"""Tests for Agent (mocked LLM provider)."""

from unittest.mock import AsyncMock

import pytest

from nova.agent.agent import Agent
from nova.llm.models import FunctionCall, Role, StreamChunk, ToolCall
from nova.security.permissions import PermissionSystem
from nova.tools.registry import ToolRegistry
from nova.tools.tool import RiskLevel, Tool


def make_streaming_provider(tokens=None, record_calls=None):
    tokens = tokens or ["Hello", " ", "world", "!"]

    async def mock_stream(messages, model="", tools=None, options=None):
        if record_calls is not None:
            record_calls.append({"model": model, "tools": tools, "options": options})
        for t in tokens:
            yield StreamChunk(token=t, done=False)
        yield StreamChunk(token="", done=True, eval_count=4)

    provider = AsyncMock()
    provider.chat_stream = mock_stream
    return provider


@pytest.mark.asyncio
async def test_send_sync():
    provider = make_streaming_provider()
    agent = Agent(provider=provider, model="test-model")

    response = await agent.send_sync("Hi")
    assert response == "Hello world!"


@pytest.mark.asyncio
async def test_send_sync_adds_to_context():
    provider = make_streaming_provider()
    agent = Agent(provider=provider, model="test-model")

    await agent.send_sync("Hi")
    msgs = agent.context.to_messages()
    assert len(msgs) == 3  # system + user + assistant
    assert msgs[1].role == Role.USER
    assert msgs[2].role == Role.ASSISTANT
    assert msgs[2].content == "Hello world!"


@pytest.mark.asyncio
async def test_send_sync_multi_turn():
    provider = make_streaming_provider()
    agent = Agent(provider=provider, model="test-model")

    await agent.send_sync("First message")
    await agent.send_sync("Second message")
    msgs = agent.context.to_messages()
    assert len(msgs) == 5  # system + 2 user + 2 assistant


@pytest.mark.asyncio
async def test_send_streaming():
    provider = make_streaming_provider()
    agent = Agent(provider=provider, model="test-model")

    tokens = []
    async for chunk in agent.send("Hi"):
        if chunk.token:
            tokens.append(chunk.token)

    assert "".join(tokens) == "Hello world!"


@pytest.mark.asyncio
async def test_send_streaming_adds_to_context():
    provider = make_streaming_provider()
    agent = Agent(provider=provider, model="test-model")

    async for _ in agent.send("Hi"):
        pass

    msgs = agent.context.to_messages()
    assert len(msgs) == 3
    assert msgs[2].content == "Hello world!"


@pytest.mark.asyncio
async def test_send_streaming_yields_done():
    provider = make_streaming_provider()
    agent = Agent(provider=provider, model="test-model")

    chunks = []
    async for chunk in agent.send("Hi"):
        chunks.append(chunk)

    assert chunks[-1].done is True
    assert chunks[-1].eval_count == 4


def test_reset():
    provider = make_streaming_provider()
    agent = Agent(provider=provider, model="test-model")

    agent.context.add_user("test")
    agent.context.add_assistant("response")
    assert len(agent.context.messages) == 2

    agent.reset()
    assert len(agent.context.messages) == 0


def test_default_model():
    provider = make_streaming_provider()
    agent = Agent(provider=provider)
    assert agent.model == ""


def test_custom_model():
    provider = make_streaming_provider()
    agent = Agent(provider=provider, model="custom-model")
    assert agent.model == "custom-model"


@pytest.mark.asyncio
async def test_send_sync_passes_model():
    calls = []
    provider = make_streaming_provider(record_calls=calls)
    agent = Agent(provider=provider, model="my-model")

    await agent.send_sync("Hi")
    assert calls[0]["model"] == "my-model"


@pytest.mark.asyncio
async def test_no_tools_when_no_registry():
    calls = []
    provider = make_streaming_provider(record_calls=calls)
    agent = Agent(provider=provider, model="test-model")

    await agent.send_sync("Hi")
    assert calls[0]["tools"] is None


@pytest.mark.asyncio
async def test_tools_schemas_passed_with_registry():
    calls = []
    provider = make_streaming_provider(record_calls=calls)
    from nova.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register_linux_tools()
    agent = Agent(provider=provider, model="test-model", registry=registry)

    await agent.send_sync("Hi")
    schemas = calls[0]["tools"]
    assert schemas is not None
    names = [s["function"]["name"] for s in schemas]
    assert "linux.cpu_info" in names

@pytest.mark.asyncio
async def test_send_passes_options_to_provider():
    calls = []
    provider = make_streaming_provider(record_calls=calls)
    agent = Agent(provider=provider, model="test-model", options={"temperature": 0.1})

    await agent.send_sync("Hi")

    assert calls[0]["options"] == {"temperature": 0.1}


@pytest.mark.asyncio
async def test_send_passes_none_options_by_default():
    calls = []
    provider = make_streaming_provider(record_calls=calls)
    agent = Agent(provider=provider, model="test-model")

    await agent.send_sync("Hi")

    assert calls[0]["options"] is None


@pytest.mark.asyncio
async def test_tool_callback_reports_start_and_end():
    events = []

    async def callback(event):
        events.append(event)

    provider = make_streaming_provider(tokens=["", ""])
    provider.chat_stream = _stream_with_tool_call_once(provider.chat_stream)
    registry = ToolRegistry()
    registry.register(Tool(
        name="echo.tool",
        description="d",
        parameters={"type": "object", "properties": {}},
        risk=RiskLevel.READ,
        permission="echo",
        implementation=lambda x: {"echo": x},
    ))
    agent = Agent(provider=provider, registry=registry)
    agent.tool_callback = callback

    await agent.send_sync("hi")

    starts = [e for e in events if e["type"] == "start"]
    ends = [e for e in events if e["type"] == "end"]
    assert len(starts) == 1
    assert starts[0]["name"] == "echo.tool"
    assert starts[0]["args"] == {"x": 1}
    assert len(ends) == 1
    assert ends[0]["ok"] is True
    assert ends[0]["duration_ms"] >= 0


def _stream_with_tool_call_once(original):
    """Wrap a chat_stream so the first iteration yields a tool call."""
    call = ToolCall(function=FunctionCall(name="echo.tool", arguments={"x": 1}))
    first = True

    async def wrapped(messages, model="", tools=None, options=None):
        nonlocal first
        if first:
            first = False
            yield StreamChunk(token="", tool_calls=[call])
            return
        async for chunk in original(messages, model, tools, options):
            yield chunk

    return wrapped

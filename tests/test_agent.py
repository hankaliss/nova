"""Tests for Agent (mocked LLM provider)."""

from unittest.mock import AsyncMock

import pytest

from nova.agent.agent import Agent
from nova.llm.models import FunctionCall, Role, StreamChunk, ToolCall
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


def _chunks(*chunks):
    async def gen():
        for c in chunks:
            yield c

    return gen()


def _tokens(text):
    return _chunks(StreamChunk(token=text, done=True))


class _FakeProvider:
    """Deterministic provider that records chat_stream calls."""

    def __init__(self, mode="native", streams=None):
        self.mode = mode
        self.streams = list(streams or [])
        self.calls = []

    async def tool_mode(self, model):
        return self.mode

    async def chat_stream(self, messages, model="", tools=None, options=None):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": tools,
                "options": options,
            }
        )
        if self.streams:
            stream = self.streams.pop(0)
            async for chunk in stream:
                yield chunk
        else:
            yield StreamChunk(token="done", done=True)


@pytest.mark.asyncio
async def test_parse_tool_calls_code_block():
    text = '```json\n{"tool": "weather.get", "args": {"city": "Paris"}}\n```'
    calls = Agent._parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].function.name == "weather.get"
    assert calls[0].function.arguments == {"city": "Paris"}


@pytest.mark.asyncio
async def test_parse_tool_calls_name_arguments_shape():
    text = 'Sure, calling now: {"name": "mcp.github.get_me", "arguments": {}}'
    calls = Agent._parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].function.name == "mcp.github.get_me"
    assert calls[0].function.arguments == {}


@pytest.mark.asyncio
async def test_parse_tool_calls_multiple_and_dedupe():
    text = (
        '```json\n{"tool": "cpu_info", "args": {}}\n```\n'
        "then:\n"
        '```json\n{"tool": "cpu_info", "args": {}}\n```\n'
        '```json\n{"tool": "mem_info", "args": {}}\n```'
    )
    calls = Agent._parse_tool_calls(text)
    assert [c.function.name for c in calls] == ["cpu_info", "mem_info"]


@pytest.mark.asyncio
async def test_parse_tool_calls_ignores_junk():
    assert Agent._parse_tool_calls("No tools here, just text.") == []
    assert Agent._parse_tool_calls("```json\nnot valid json\n```") == []
    assert Agent._parse_tool_calls('{"tool": 123}') == []


@pytest.mark.asyncio
async def test_tool_instructions_include_schemas():
    registry = ToolRegistry()
    registry.register_linux_tools()
    agent = Agent(provider=_FakeProvider(), registry=registry, model="m")
    instructions = agent._tool_instructions()
    assert "Available tools:" in instructions
    assert "linux.cpu_info" in instructions
    assert '{"tool": "tool_name", "args": {"key": "value"}}' in instructions


@pytest.mark.asyncio
async def test_generate_text_mode_executes_parsed_tool():
    executed = []

    def echo(x):
        executed.append(x)
        return {"echo": x}

    first = _tokens(
        '```json\n{"tool": "echo.tool", "args": {"x": 5}}\n```'
    )
    second = _tokens("The result is 5.")
    provider = _FakeProvider(mode="prompt", streams=[first, second])
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="echo.tool",
            description="echo",
            parameters={"type": "object", "properties": {"x": {"type": "integer"}}},
            risk=RiskLevel.READ,
            implementation=echo,
        )
    )
    agent = Agent(provider=provider, registry=registry, model="qwen2.5-coder:7b")
    events = []
    agent.tool_callback = lambda e: events.append(e)

    response = await agent.send_sync("do the thing")

    assert "The result is 5." in response
    assert executed == [5]
    starts = [e for e in events if e["type"] == "start"]
    ends = [e for e in events if e["type"] == "end"]
    assert len(starts) == 1 and starts[0]["name"] == "echo.tool"
    assert len(ends) == 1 and ends[0]["ok"] is True

    roles = [m.role for m in agent.context.messages]
    contents = [m.content for m in agent.context.messages]
    assert roles == [Role.USER, Role.ASSISTANT, Role.TOOL, Role.ASSISTANT]
    assert any("The result is 5." in c for c in contents)


@pytest.mark.asyncio
async def test_generate_native_uses_structured_and_no_instructions():
    call = ToolCall(function=FunctionCall(name="echo.tool", arguments={"x": 1}))
    first_stream = _chunks(StreamChunk(token="", tool_calls=[call]))

    def echo(x):
        return {"echo": x}

    second_stream = _tokens("done here")
    provider = _FakeProvider(mode="native", streams=[first_stream, second_stream])
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="echo.tool",
            description="echo",
            parameters={"type": "object", "properties": {}},
            risk=RiskLevel.READ,
            implementation=echo,
        )
    )
    agent = Agent(provider=provider, registry=registry, model="qwen3:8b")

    response = await agent.send_sync("hi")

    assert response == "done here"
    first = provider.calls[0]
    assert first["tools"] is not None
    names = [s["function"]["name"] for s in first["tools"]]
    assert "echo.tool" in names
    system = first["messages"][0].content
    assert "Available tools:" not in system


@pytest.mark.asyncio
async def test_generate_prompt_mode_sends_tools_and_instructions():
    provider = _FakeProvider(mode="prompt", streams=[_tokens("ok")])
    registry = ToolRegistry()
    registry.register_linux_tools()
    agent = Agent(provider=provider, registry=registry, model="qwen2.5-coder:7b")

    await agent.send_sync("hi")

    first = provider.calls[0]
    assert first["tools"] is not None
    system = first["messages"][0].content
    assert "Available tools:" in system
    assert "linux.cpu_info" in system


@pytest.mark.asyncio
async def test_generate_unsupported_mode_sends_no_tools():
    provider = _FakeProvider(mode="unsupported", streams=[_tokens("ok")])
    registry = ToolRegistry()
    registry.register_linux_tools()
    agent = Agent(provider=provider, registry=registry, model="llama2")

    await agent.send_sync("hi")

    first = provider.calls[0]
    assert first["tools"] is None
    system = first["messages"][0].content
    assert "Available tools:" in system


@pytest.mark.asyncio
async def test_generate_multi_tool_loop_limits():
    tool_text = '{"tool": "echo.tool", "args": {}}'
    provider = _FakeProvider(
        mode="unsupported", streams=[_tokens(tool_text) for _ in range(12)]
    )
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="echo.tool",
            description="echo",
            parameters={"type": "object", "properties": {}},
            risk=RiskLevel.READ,
            implementation=lambda: {"echo": 1},
        )
    )
    agent = Agent(
        provider=provider, registry=registry, model="llama2", max_tool_iterations=3
    )

    response = await agent.send_sync("loop")
    assert "Tool call limit exceeded" in response
    assert len(provider.calls) == 3


# ---- Context budget --------------------------------------------------------


@pytest.mark.asyncio
async def test_long_history_is_fitted_to_num_ctx():
    sent = []

    async def mock_stream(messages, model="", tools=None, options=None):
        sent.append(messages)
        yield StreamChunk(token="ok", done=True)

    provider = AsyncMock()
    provider.chat_stream = mock_stream
    agent = Agent(provider=provider, options={"num_ctx": 2048})
    for i in range(40):
        agent.context.add_user(f"q{i} " + "x" * 400)
        agent.context.add_assistant(f"a{i} " + "y" * 400)

    await agent.send_sync("latest question")

    from nova.agent.context import estimate_message_tokens

    msgs = sent[0]
    assert msgs[0].role == Role.SYSTEM
    assert msgs[-1].content == "latest question"
    assert len(msgs) < 82
    assert sum(estimate_message_tokens(m) for m in msgs) <= 2048 - 512


@pytest.mark.asyncio
async def test_tool_schemas_reduce_history_budget():
    sent = []

    async def mock_stream(messages, model="", tools=None, options=None):
        sent.append(messages)
        yield StreamChunk(token="ok", done=True)

    def build(registry):
        provider = AsyncMock()
        provider.chat_stream = mock_stream
        agent = Agent(provider=provider, registry=registry, options={"num_ctx": 4096})
        for i in range(60):
            agent.context.add_user(f"q{i} " + "x" * 200)
            agent.context.add_assistant(f"a{i} " + "y" * 200)
        return agent

    await build(None).send_sync("hi")
    registry = ToolRegistry()
    registry.register_linux_tools()
    registry.register_web_tools()
    await build(registry).send_sync("hi")
    assert len(sent[1]) < len(sent[0])


@pytest.mark.asyncio
async def test_large_tool_result_is_truncated_in_context():
    calls = 0

    async def mock_stream(messages, model="", tools=None, options=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield StreamChunk(
                done=True,
                tool_calls=[ToolCall(function=FunctionCall(name="big.dump", arguments={}))],
            )
        else:
            yield StreamChunk(token="done", done=True)

    provider = AsyncMock()
    provider.chat_stream = mock_stream
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="big.dump",
            description="huge output",
            risk=RiskLevel.READ,
            implementation=lambda: "START" + "x" * 200_000 + "END",
        )
    )
    agent = Agent(provider=provider, registry=registry, options={"num_ctx": 8192})
    await agent.send_sync("dump it")

    tool_msg = next(m for m in agent.context.messages if m.role == Role.TOOL)
    assert len(tool_msg.content) <= 8192 // 4 * 3
    assert "START" in tool_msg.content and "characters truncated" in tool_msg.content


@pytest.mark.asyncio
async def test_text_mode_rounds_are_separated_in_stream():
    """A text-mode tool call's closing fence must not touch the next round's text."""
    calls = 0

    async def mock_stream(messages, model="", tools=None, options=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield StreamChunk(token='Checking:\n\n```json\n{"tool": "echo.it", "args": {}}\n```')
            yield StreamChunk(token="", done=True)
        else:
            yield StreamChunk(token="It seems fine.", done=True)

    async def tool_mode(model):
        return "unsupported"

    provider = AsyncMock()
    provider.chat_stream = mock_stream
    provider.tool_mode = tool_mode
    registry = ToolRegistry()
    registry.register(
        Tool(name="echo.it", description="echo", risk=RiskLevel.READ,
             implementation=lambda: "ok")
    )
    agent = Agent(provider=provider, registry=registry)

    text = await agent.send_sync("check")

    assert calls == 2
    # The call itself is hidden (AD-037); the prose around it stays apart.
    assert text == "Checking:\n\nIt seems fine."

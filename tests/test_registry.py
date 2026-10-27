"""Tests for ToolRegistry."""

import asyncio

import pytest

from nova.tools.registry import ToolRegistry
from nova.tools.tool import RiskLevel, Tool


def make_tool(name="test.tool", risk=RiskLevel.READ):
    def impl():
        return "ok"

    return Tool(
        name=name,
        description=f"Tool {name}",
        implementation=impl,
        risk=risk,
    )


def test_register_and_get():
    registry = ToolRegistry()
    tool = make_tool()
    registry.register(tool)
    assert registry.get("test.tool") == tool


def test_duplicate_register_raises():
    registry = ToolRegistry()
    registry.register(make_tool())
    with pytest.raises(ValueError):
        registry.register(make_tool())


def test_get_unknown_tool_raises():
    registry = ToolRegistry()
    with pytest.raises(KeyError):
        registry.get("missing.tool")


def test_list_tools():
    registry = ToolRegistry()
    registry.register(make_tool("a.tool"))
    registry.register(make_tool("b.tool"))
    assert len(registry.list_tools()) == 2


def test_to_schemas_format():
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="test.add",
            description="Add two numbers",
            parameters={
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
                "required": ["a", "b"],
            },
            implementation=lambda a, b: a + b,
        )
    )
    schemas = registry.to_schemas()
    assert len(schemas) == 1
    assert schemas[0]["type"] == "function"
    fn = schemas[0]["function"]
    assert fn["name"] == "test.add"
    assert fn["description"] == "Add two numbers"
    assert fn["parameters"]["required"] == ["a", "b"]


def test_to_schemas_empty_schema():
    registry = ToolRegistry()
    registry.register(make_tool())
    schemas = registry.to_schemas()
    assert schemas[0]["function"]["parameters"]["type"] == "object"


@pytest.mark.asyncio
async def test_execute_read_tool():
    registry = ToolRegistry()
    registry.register(make_tool("test.read"))
    result = await registry.execute("test.read")
    assert result.success is True
    assert result.data == "ok"


@pytest.mark.asyncio
async def test_execute_write_tool_blocked():
    registry = ToolRegistry()
    registry.register(make_tool("test.write", RiskLevel.WRITE))
    result = await registry.execute("test.write")
    assert result.success is False
    assert "permission" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_execute_destructive_tool_blocked():
    registry = ToolRegistry()
    registry.register(make_tool("test.destructive", RiskLevel.DESTRUCTIVE))
    result = await registry.execute("test.destructive")
    assert result.success is False
    assert "confirmation" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_execute_with_args():
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="test.add",
            description="Add",
            implementation=lambda a, b: a + b,
        )
    )
    result = await registry.execute("test.add", {"a": 2, "b": 3})
    assert result.success is True
    assert result.data == 5


@pytest.mark.asyncio
async def test_execute_unknown_tool():
    registry = ToolRegistry()
    result = await registry.execute("missing.tool")
    assert result.success is False
    assert "Unknown tool" in (result.error or "")


@pytest.mark.asyncio
async def test_execute_tool_exception():
    registry = ToolRegistry()

    def boom():
        raise RuntimeError("exploded")

    registry.register(Tool(name="test.boom", description="Boom", implementation=boom))
    result = await registry.execute("test.boom")
    assert result.success is False
    assert result.error == "exploded"


@pytest.mark.asyncio
async def test_execute_async_tool():
    registry = ToolRegistry()

    async def async_impl():
        return "async"

    registry.register(
        Tool(name="test.async", description="Async", implementation=async_impl)
    )
    result = await registry.execute("test.async")
    assert result.success is True
    assert result.data == "async"


def test_register_linux_tools():
    registry = ToolRegistry()
    registry.register_linux_tools()
    names = [t.name for t in registry.list_tools()]
    assert "linux.cpu_info" in names
    assert "linux.memory_info" in names
    assert "linux.gpu_info" in names
    assert "linux.disk_usage" in names
    assert "linux.uptime" in names

def test_unregister_removes_tool():
    registry = ToolRegistry()
    tool = Tool(
        name="test.tool",
        description="d",
        parameters={"type": "object", "properties": {}},
        risk=RiskLevel.READ,
        permission="test",
        implementation=lambda: "ok",
    )
    registry.register(tool)
    assert registry.unregister("test.tool") is True
    with pytest.raises(KeyError):
        registry.get("test.tool")
    assert registry.unregister("test.tool") is False


# ---- Hidden tools and skill calls (AD-042) --------------------------------


def test_hidden_tool_not_in_schemas_and_not_executable_by_model():
    registry = ToolRegistry()
    hidden = make_tool("mcp.gh.list_issues")
    hidden.model_visible = False
    registry.register(hidden)
    registry.register(make_tool("test.visible"))

    assert [s["function"]["name"] for s in registry.to_schemas()] == ["test.visible"]
    result = asyncio.run(registry.execute("mcp.gh.list_issues"))
    assert not result.success and "Unknown tool" in result.error


def test_skill_can_call_hidden_read_tool_but_not_write():
    registry = ToolRegistry()
    hidden = make_tool("mcp.gh.list_issues")
    hidden.model_visible = False
    registry.register(hidden)
    registry.register(make_tool("mcp.gh.issue_write", risk=RiskLevel.WRITE))

    assert asyncio.run(registry.execute_for_skill("mcp.gh.list_issues")).data == "ok"
    refused = asyncio.run(registry.execute_for_skill("mcp.gh.issue_write"))
    assert not refused.success and "not a READ tool" in refused.error
    assert not asyncio.run(registry.execute_for_skill("nope")).success


def test_skill_host_finds_and_calls_mcp_tools():
    from nova.skills import host

    registry = ToolRegistry()
    registry.register(make_tool("mcp.github.list_issues"))
    registry.register(make_tool("linux.list_issues"))  # not MCP: never matched
    host.attach(registry)
    try:
        assert host.find_mcp_tool("list_issues") == "mcp.github.list_issues"
        assert host.find_mcp_tool("missing") is None
        assert asyncio.run(host.call_tool("mcp.github.list_issues")) == "ok"
        with pytest.raises(RuntimeError, match="Unknown tool"):
            asyncio.run(host.call_tool("mcp.github.nope"))
    finally:
        host.attach(None)


def test_skill_host_times_out_slow_tools():
    from nova.skills import host

    async def slow():
        await asyncio.sleep(5)

    registry = ToolRegistry()
    registry.register(Tool(name="mcp.x.slow", description="", implementation=slow))
    host.attach(registry)
    try:
        with pytest.raises(RuntimeError, match="did not answer"):
            asyncio.run(host.call_tool("mcp.x.slow", timeout=0.05))
    finally:
        host.attach(None)

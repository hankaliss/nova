"""Tests for PermissionSystem."""


import pytest

from nova.security.permissions import PermissionDenied, PermissionSystem
from nova.tools.registry import ToolRegistry
from nova.tools.tool import RiskLevel, Tool


def make_tool(name="test.tool", risk=RiskLevel.READ, permission="test.perm"):
    def impl():
        return "ok"

    return Tool(
        name=name,
        description=f"Tool {name}",
        implementation=impl,
        risk=risk,
        permission=permission,
    )


def test_read_tools_always_allowed():
    perms = PermissionSystem()
    assert perms.can_execute(make_tool(risk=RiskLevel.READ)) is True


def test_write_tools_denied_by_default():
    perms = PermissionSystem()
    assert perms.can_execute(make_tool(risk=RiskLevel.WRITE)) is False


def test_destructive_always_requires_confirmation():
    perms = PermissionSystem()
    assert perms.can_execute(make_tool(risk=RiskLevel.DESTRUCTIVE)) is False


def test_auto_approve_writes():
    perms = PermissionSystem(auto_approve_writes=True)
    assert perms.can_execute(make_tool(risk=RiskLevel.WRITE)) is True
    assert perms.can_execute(make_tool(risk=RiskLevel.DESTRUCTIVE)) is False


def test_session_grant():
    perms = PermissionSystem()
    tool = make_tool(risk=RiskLevel.WRITE, permission="test.perm")
    assert perms.can_execute(tool) is False
    perms.grant("test.perm")
    assert perms.can_execute(tool) is True
    perms.revoke("test.perm")
    assert perms.can_execute(tool) is False


@pytest.mark.asyncio
async def test_no_callback_always_denies_confirmation():
    perms = PermissionSystem()
    tool = make_tool(risk=RiskLevel.DESTRUCTIVE)
    assert await perms.request_confirmation(tool, {}) is False


@pytest.mark.asyncio
async def test_callback_approves():
    async def approve(tool, args):
        return True

    perms = PermissionSystem(approval_callback=approve)
    tool = make_tool(risk=RiskLevel.DESTRUCTIVE)
    assert await perms.request_confirmation(tool, {}) is True


@pytest.mark.asyncio
async def test_callback_denies():
    async def deny(tool, args):
        return False

    perms = PermissionSystem(approval_callback=deny)
    tool = make_tool(risk=RiskLevel.DESTRUCTIVE)
    assert await perms.request_confirmation(tool, {}) is False


@pytest.mark.asyncio
async def test_sync_callback_supported():
    def approve(tool, args):
        return True

    perms = PermissionSystem(approval_callback=approve)
    tool = make_tool(risk=RiskLevel.DESTRUCTIVE)
    assert await perms.request_confirmation(tool, {}) is True


@pytest.mark.asyncio
async def test_authorize_read_passes():
    perms = PermissionSystem()
    await perms.authorize(make_tool(risk=RiskLevel.READ), {})


@pytest.mark.asyncio
async def test_authorize_write_denied_without_callback():
    perms = PermissionSystem()
    with pytest.raises(PermissionDenied, match="WRITE"):
        await perms.authorize(make_tool(risk=RiskLevel.WRITE), {})


@pytest.mark.asyncio
async def test_authorize_destructive_denied_without_callback():
    perms = PermissionSystem()
    with pytest.raises(PermissionDenied, match="confirmation"):
        await perms.authorize(make_tool(risk=RiskLevel.DESTRUCTIVE), {})


@pytest.mark.asyncio
async def test_authorize_write_approved_via_callback():
    async def approve(tool, args):
        return True

    perms = PermissionSystem(approval_callback=approve)
    await perms.authorize(make_tool(risk=RiskLevel.WRITE), {})


@pytest.mark.asyncio
async def test_authorize_destructive_approved_via_callback():
    async def approve(tool, args):
        return True

    perms = PermissionSystem(approval_callback=approve)
    await perms.authorize(make_tool(risk=RiskLevel.DESTRUCTIVE), {})


@pytest.mark.asyncio
async def test_registry_executes_destructive_after_approval():
    async def approve(tool, args):
        return True

    perms = PermissionSystem(approval_callback=approve)
    registry = ToolRegistry(permissions=perms)
    registry.register(make_tool("test.destructive", RiskLevel.DESTRUCTIVE))

    result = await registry.execute("test.destructive")
    assert result.success is True
    assert result.data == "ok"


@pytest.mark.asyncio
async def test_registry_denies_destructive_without_approval():
    registry = ToolRegistry()
    registry.register(make_tool("test.destructive", RiskLevel.DESTRUCTIVE))

    result = await registry.execute("test.destructive")
    assert result.success is False
    assert "confirmation" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_registry_denies_write_without_approval():
    registry = ToolRegistry()
    registry.register(make_tool("test.write", RiskLevel.WRITE))

    result = await registry.execute("test.write")
    assert result.success is False
    assert "permission" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_registry_write_with_session_grant():
    perms = PermissionSystem()
    perms.grant("test.perm")
    registry = ToolRegistry(permissions=perms)
    registry.register(make_tool("test.write", RiskLevel.WRITE, "test.perm"))

    result = await registry.execute("test.write")
    assert result.success is True


@pytest.mark.asyncio
async def test_approval_callback_receives_tool_and_args():
    received = {}

    async def approve(tool, args):
        received["tool"] = tool.name
        received["args"] = args
        return True

    perms = PermissionSystem(approval_callback=approve)
    registry = ToolRegistry(permissions=perms)
    registry.register(make_tool("test.destructive", RiskLevel.DESTRUCTIVE))

    await registry.execute("test.destructive", {"a": 1})
    assert received["tool"] == "test.destructive"
    assert received["args"] == {"a": 1}

def test_grant_never_bypasses_destructive():
    perms = PermissionSystem(auto_approve_writes=True)
    tool = make_tool(risk=RiskLevel.DESTRUCTIVE, permission="test.perm")
    perms.grant("test.perm")
    assert perms.can_execute(tool) is False


def test_auto_approve_skips_non_auto_approvable_write():
    perms = PermissionSystem(auto_approve_writes=True)
    tool = make_tool(risk=RiskLevel.WRITE).model_copy(
        update={"auto_approvable": False}
    )
    assert perms.can_execute(tool) is False
    perms.grant(tool.permission)
    assert perms.can_execute(tool) is True

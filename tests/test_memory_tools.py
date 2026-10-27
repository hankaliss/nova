"""Tests for the memory tools and how memories reach the prompt."""

import pytest

from nova.agent.context import ConversationContext
from nova.memory.store import MemoryStore
from nova.security.permissions import PermissionSystem
from nova.tools.registry import ToolRegistry
from nova.tools.tool import RiskLevel


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "nova.db")


def registry_for(store, approve=True):
    registry = ToolRegistry(
        permissions=PermissionSystem(approval_callback=lambda tool, args: approve)
    )
    registry.register_memory_tools(store)
    return registry


def test_memory_tool_risks(store):
    tools = {t.name: t for t in ToolRegistry().register_memory_tools(store)}
    assert tools["memory.remember"].risk == RiskLevel.WRITE
    assert tools["memory.remember"].auto_approvable is False   # injection guard
    assert tools["memory.search"].risk == RiskLevel.READ
    assert tools["memory.forget"].risk == RiskLevel.DESTRUCTIVE


@pytest.mark.asyncio
async def test_remember_search_forget_roundtrip(store):
    registry = registry_for(store)
    saved = await registry.execute("memory.remember", {"fact": "The NAS is at 192.168.1.20"})
    assert saved.success and "Saved memory #" in saved.data
    found = await registry.execute("memory.search", {"query": "nas address"})
    assert found.success and found.data[0]["fact"] == "The NAS is at 192.168.1.20"
    memory_id = found.data[0]["id"]
    gone = await registry.execute("memory.forget", {"memory_id": memory_id})
    assert gone.success
    assert (await registry.execute("memory.search", {"query": "nas"})).data == []
    missing = await registry.execute("memory.forget", {"memory_id": memory_id})
    assert not missing.success


@pytest.mark.asyncio
async def test_remember_is_refused_without_approval_even_with_auto_approve(store):
    registry = registry_for(store, approve=False)
    registry.permissions.auto_approve_writes = True
    result = await registry.execute("memory.remember", {"fact": "planted by a web page"})
    assert not result.success
    assert store.list_memories() == []


@pytest.mark.asyncio
async def test_empty_search_lists_recent(store):
    registry = registry_for(store)
    for fact in ("one fact", "two fact", "three fact"):
        store.save_memory(fact)
    listed = (await registry.execute("memory.search", {})).data
    assert len(listed) == 3


def test_memory_note_rides_in_the_system_message():
    ctx = ConversationContext(system_prompt="SYS")
    ctx.memory_note = "## Things the user asked you to remember\n- The NAS is at 192.168.1.20"
    ctx.add_user("what's my nas ip?")
    system = ctx.to_messages(max_tokens=10_000)[0]
    assert system.content.startswith("SYS") and "192.168.1.20" in system.content
    ctx.memory_note = ""
    assert ctx.to_messages()[0].content == "SYS"

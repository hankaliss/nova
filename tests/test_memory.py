"""Tests for MemoryStore (SQLite)."""

import pytest

from nova.llm.models import Role
from nova.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "test.db")


def test_create_conversation(store):
    conv_id = store.create_conversation()
    assert conv_id > 0
    convs = store.list_conversations()
    assert len(convs) == 1
    assert convs[0]["id"] == conv_id


def test_list_conversations_empty(store):
    assert store.list_conversations() == []


def test_add_and_get_messages(store):
    conv_id = store.create_conversation()
    store.add_message(conv_id, Role.USER, "Hello")
    store.add_message(conv_id, Role.ASSISTANT, "Hi there")

    messages = store.get_messages(conv_id)
    assert len(messages) == 2
    assert messages[0].role == Role.USER
    assert messages[0].content == "Hello"
    assert messages[1].role == Role.ASSISTANT
    assert messages[1].content == "Hi there"


def test_get_messages_unknown_conversation(store):
    assert store.get_messages(999) == []


def test_messages_isolated_between_conversations(store):
    c1 = store.create_conversation()
    c2 = store.create_conversation()
    store.add_message(c1, Role.USER, "in first")
    store.add_message(c2, Role.USER, "in second")

    assert len(store.get_messages(c1)) == 1
    assert store.get_messages(c1)[0].content == "in first"
    assert store.get_messages(c2)[0].content == "in second"


def test_delete_conversation_cascades(store):
    conv_id = store.create_conversation()
    store.add_message(conv_id, Role.USER, "Hello")
    store.delete_conversation(conv_id)

    assert store.list_conversations() == []
    assert store.get_messages(conv_id) == []


def test_auto_title_from_first_message(store):
    conv_id = store.create_conversation()
    assert store.auto_title(conv_id, "How is the system doing?") is True
    convs = store.list_conversations()
    assert convs[0]["title"] == "How is the system doing?"


def test_auto_title_strips_markdown_and_truncates(store):
    conv_id = store.create_conversation()
    long = "## GPU memory: " + "x" * 100
    assert store.auto_title(conv_id, long) is True
    title = store.list_conversations()[0]["title"]
    assert "##" not in title
    assert len(title) <= 49
    assert title.endswith("…")


def test_auto_title_does_not_override_manual_title(store):
    conv_id = store.create_conversation()
    store.rename_conversation(conv_id, "My manual title")
    assert store.auto_title(conv_id, "First user message") is False
    assert store.list_conversations()[0]["title"] == "My manual title"


def test_auto_title_empty_content(store):
    conv_id = store.create_conversation()
    assert store.auto_title(conv_id, "   ") is False


def test_save_and_list_memories(store):
    m1 = store.save_memory("User prefers concise answers")
    m2 = store.save_memory("User uses CachyOS")

    memories = store.list_memories()
    assert len(memories) == 2
    ids = {m["id"] for m in memories}
    assert ids == {m1, m2}
    contents = {m["content"] for m in memories}
    assert "User uses CachyOS" in contents


def test_delete_memory(store):
    mid = store.save_memory("Temporary note")
    store.delete_memory(mid)
    assert store.list_memories() == []


def test_setting_set_get(store):
    assert store.get_setting("theme") is None
    assert store.get_setting("theme", "dark") == "dark"

    store.set_setting("theme", "dark")
    assert store.get_setting("theme") == "dark"


def test_setting_overwrite(store):
    store.set_setting("model", "qwen3:8b")
    store.set_setting("model", "other")
    assert store.get_setting("model") == "other"


def test_multiple_settings(store):
    store.set_setting("a", "1")
    store.set_setting("b", "2")
    assert store.get_setting("a") == "1"
    assert store.get_setting("b") == "2"


def test_persistence_across_instances(tmp_path):
    db = tmp_path / "persist.db"
    s1 = MemoryStore(db)
    conv_id = s1.create_conversation()
    s1.add_message(conv_id, Role.USER, "Remember this")
    s1.save_memory("Persistent memory")
    s1.set_setting("theme", "dark")

    s2 = MemoryStore(db)
    assert len(s2.get_messages(conv_id)) == 1
    assert s2.get_messages(conv_id)[0].content == "Remember this"
    assert len(s2.list_memories()) == 1
    assert s2.get_setting("theme") == "dark"


def test_close_noop(store):
    store.close()  # should not raise


def test_default_path_is_xdg():
    store = MemoryStore()
    assert "nova" in str(store.db_path)

def test_delete_last_message(store):
    cid = store.create_conversation()
    assert store.delete_last_message(cid) is False

    store.add_message(cid, Role.USER, "u")
    store.add_message(cid, Role.ASSISTANT, "a")
    assert store.delete_last_message(cid) is True
    assert [m.role for m in store.get_messages(cid)] == [Role.USER]

    assert store.delete_last_message(cid) is True
    assert store.get_messages(cid) == []

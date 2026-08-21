"""Tests for Python-QML ChatBridge."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nova.llm.models import Role, StreamChunk
from nova.memory.store import MemoryStore
from nova.tools.tool import RiskLevel, Tool
from nova.ui.bridge import ChatBridge


@pytest.fixture
def mock_agent():
    agent = MagicMock()

    async def mock_send(text):
        yield StreamChunk(token="Hello", done=False)
        yield StreamChunk(token=" world", done=False)
        yield StreamChunk(token="!", done=True)

    agent.send = mock_stream_send = mock_send
    agent.reset = MagicMock()
    agent.model = None
    return agent


@pytest.fixture
def bridge(mock_agent):
    return ChatBridge(agent=mock_agent)


@pytest.mark.asyncio
async def test_send_message_emits_signals(bridge):
    # Track emitted signals
    messages = []
    tokens = []
    completed = []

    bridge.messageAdded.connect(lambda r, c, s: messages.append((r, c, s)))
    bridge.streamUpdated.connect(lambda t: tokens.append(t))
    bridge.streamCompleted.connect(lambda: completed.append(True))

    await bridge.sendMessage("Hi")

    # Check user message emitted
    assert len(messages) == 2
    assert messages[0] == ("user", "Hi", False)
    assert messages[1] == ("assistant", "", True)

    # Check tokens emitted
    assert tokens == ["Hello", " world", "!"]

    # Check stream completed emitted
    assert completed == [True]


@pytest.mark.asyncio
async def test_send_message_handles_error(bridge):
    # Force agent to raise an error
    async def mock_error_send(text):
        raise ValueError("Ollama offline")
        yield StreamChunk(token="", done=True)  # needed to keep it a generator

    bridge.agent.send = mock_error_send

    errors = []
    completed = []

    bridge.statusChanged.connect(lambda msg: errors.append(msg))
    bridge.streamCompleted.connect(lambda: completed.append(True))

    await bridge.sendMessage("Hi")

    assert len(errors) == 1
    assert "Ollama offline" in errors[0]
    assert completed == [True]


def test_clear_history(bridge):
    bridge.clearHistory()
    bridge.agent.reset.assert_called_once()


# ---- Permission approval ------------------------------------------------


def make_tool(risk=RiskLevel.WRITE, name="test.tool"):
    return Tool(
        name=name,
        description="Test tool",
        implementation=lambda: "ok",
        risk=risk,
        permission="test.perm",
    )


@pytest.mark.asyncio
async def test_request_permission_emits_signal(bridge):
    emitted = []
    bridge.permissionRequested.connect(
        lambda t, r, p, a: emitted.append((t, r, p, a))
    )

    task = asyncio.create_task(bridge.request_permission(make_tool(), {"a": 1}))
    await asyncio.sleep(0)

    assert len(emitted) == 1
    tool_name, risk, permission, args = emitted[0]
    assert tool_name == "test.tool"
    assert risk == "write"
    assert permission == "test.perm"
    assert args == '{"a": 1}'

    bridge.respondPermission(True)
    assert await task is True


@pytest.mark.asyncio
async def test_request_permission_denied(bridge):
    task = asyncio.create_task(bridge.request_permission(make_tool(), {}))
    await asyncio.sleep(0)

    bridge.respondPermission(False)
    assert await task is False


@pytest.mark.asyncio
async def test_respond_permission_without_pending_is_noop(bridge):
    bridge.respondPermission(True)  # should not raise


@pytest.mark.asyncio
async def test_second_request_denied_while_pending(bridge):
    task1 = asyncio.create_task(bridge.request_permission(make_tool(), {}))
    await asyncio.sleep(0)

    task2 = asyncio.create_task(bridge.request_permission(make_tool(), {}))
    assert await task2 is False

    bridge.respondPermission(True)
    assert await task1 is True


# ---- Model selection ----------------------------------------------------


@pytest.mark.asyncio
async def test_list_models_emits_names(mock_agent):
    async def mock_list_models():
        return [SimpleNamespace(name="qwen3:8b"), SimpleNamespace(name="qwen2.5-coder:7b")]

    mock_agent.provider.list_models = mock_list_models
    chat = ChatBridge(agent=mock_agent)

    listed = []
    chat.modelsListed.connect(lambda names: listed.append(names))

    await chat.listModels()

    assert len(listed) == 1
    assert listed[0] == ["qwen3:8b", "qwen2.5-coder:7b"]


@pytest.mark.asyncio
async def test_list_models_emits_status_on_error(mock_agent):
    async def mock_fail():
        raise RuntimeError("Ollama offline")

    mock_agent.provider.list_models = mock_fail
    chat = ChatBridge(agent=mock_agent)

    status = []
    chat.statusChanged.connect(lambda msg: status.append(msg))

    await chat.listModels()

    assert len(status) == 1
    assert "Ollama offline" in status[0]


def test_select_model_sets_agent_and_persists(mock_agent, tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    chat = ChatBridge(agent=mock_agent, store=store)

    chat.selectModel("qwen2.5-coder:7b")

    assert mock_agent.model == "qwen2.5-coder:7b"
    assert store.get_setting("model") == "qwen2.5-coder:7b"


def test_restore_settings_applies_stored_model(mock_agent, tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.set_setting("model", "qwen3:8b")
    chat = ChatBridge(agent=mock_agent, store=store)

    chat.restore_settings()

    assert mock_agent.model == "qwen3:8b"


def test_restore_settings_noop_without_store(bridge, mock_agent):
    bridge.restore_settings()
    assert mock_agent.model is None


def test_current_model(bridge, mock_agent):
    assert bridge.currentModel() == "(default)"
    mock_agent.model = "qwen3:8b"
    assert bridge.currentModel() == "qwen3:8b"


# ---- Quit / tray ----------------------------------------------------------


def test_allow_close_defaults_false(bridge):
    assert bridge.allowClose() is False


def test_quit_app_uses_injected_callback(mock_agent):
    calls = []
    chat = ChatBridge(agent=mock_agent, quit_cb=lambda: calls.append("quit"))

    chat.quitApp()

    assert chat.allowClose() is True
    assert calls == ["quit"]


# ---- Memory integration --------------------------------------------------


def test_start_conversation_without_store(bridge):
    bridge.start_conversation()
    assert bridge._conversation_id is None


def test_load_history_without_store_is_noop(bridge, mock_agent):
    bridge.load_history()
    mock_agent.context.add_user.assert_not_called()


@pytest.mark.asyncio
async def test_send_message_persists(mock_agent, tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    chat = ChatBridge(agent=mock_agent, store=store)
    chat.start_conversation()

    await chat.sendMessage("Hi")

    conversations = store.list_conversations()
    assert len(conversations) == 1
    messages = store.get_messages(conversations[0]["id"])
    assert [(m.role, m.content) for m in messages] == [
        (Role.USER, "Hi"),
        (Role.ASSISTANT, "Hello world!"),
    ]


@pytest.mark.asyncio
async def test_send_message_persists_partial_on_error(mock_agent, tmp_path):
    async def mock_error_send(text):
        yield StreamChunk(token="partial", done=False)
        raise ValueError("Ollama offline")

    mock_agent.send = mock_error_send
    store = MemoryStore(tmp_path / "m.db")
    chat = ChatBridge(agent=mock_agent, store=store)
    chat.start_conversation()

    await chat.sendMessage("Hi")

    conversations = store.list_conversations()
    messages = store.get_messages(conversations[0]["id"])
    assert [(m.role, m.content) for m in messages] == [
        (Role.USER, "Hi"),
        (Role.ASSISTANT, "partial"),
    ]


def test_load_history_seeds_ui_and_context(mock_agent, tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    conv = store.create_conversation()
    store.add_message(conv, Role.USER, "question")
    store.add_message(conv, Role.ASSISTANT, "answer")

    chat = ChatBridge(agent=mock_agent, store=store)
    emitted = []
    chat.messageAdded.connect(lambda r, c, s: emitted.append((r, c)))

    chat.load_history()

    assert emitted == [("user", "question"), ("assistant", "answer")]
    mock_agent.context.add_user.assert_called_once_with("question")
    mock_agent.context.add_assistant.assert_called_once_with("answer")
    assert chat._conversation_id == conv


def test_load_history_creates_conversation_when_empty(mock_agent, tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    chat = ChatBridge(agent=mock_agent, store=store)

    chat.load_history()

    assert chat._conversation_id is not None
    mock_agent.context.add_user.assert_not_called()


def test_clear_history_deletes_conversation(mock_agent, tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    conv = store.create_conversation()
    store.add_message(conv, Role.USER, "question")

    chat = ChatBridge(agent=mock_agent, store=store)
    chat.load_history()
    assert len(store.list_conversations()) == 1

    chat.clearHistory()

    conversations = store.list_conversations()
    assert len(conversations) == 1
    assert conversations[0]["id"] != conv
    assert store.get_messages(conversations[0]["id"]) == []
    assert chat._conversation_id == conversations[0]["id"]
    mock_agent.reset.assert_called_once()

def test_save_settings_roundtrip_and_applies(bridge, mock_agent, tmp_path):
    store = MemoryStore(tmp_path / "nova.db")
    bridge.store = store

    bridge.saveSettings(
        '{"temperature": "0.2", "num_ctx": "8192", "system_prompt": "Be brief"}'
    )

    got = json.loads(bridge.getSettings())
    assert got["temperature"] == "0.2"
    assert got["num_ctx"] == "8192"
    assert got["system_prompt"] == "Be brief"
    assert mock_agent.options == {"temperature": 0.2, "num_ctx": 8192}
    assert mock_agent.context.system_prompt == "Be brief"
    assert store.get_setting("temperature") == "0.2"


def test_save_settings_ignores_unknown_keys(bridge, mock_agent):
    bridge.saveSettings('{"not_a_setting": "x"}')
    assert "not_a_setting" not in json.loads(bridge.getSettings())


def test_save_settings_toggles_auto_approve_writes(tmp_path):
    from nova.security.permissions import PermissionSystem

    agent = MagicMock()
    agent.registry = SimpleNamespace(permissions=PermissionSystem())
    bridge = ChatBridge(agent=agent, store=MemoryStore(tmp_path / "nova.db"))

    bridge.saveSettings('{"auto_approve_writes": "true"}')
    assert agent.registry.permissions.auto_approve_writes is True

    bridge.saveSettings('{"auto_approve_writes": "false"}')
    assert agent.registry.permissions.auto_approve_writes is False


def test_allow_close_honors_close_to_tray(bridge, tmp_path):
    store = MemoryStore(tmp_path / "nova.db")
    bridge.store = store

    bridge.saveSettings('{"close_to_tray": "false"}')
    assert bridge.allowClose() is True

    bridge.saveSettings('{"close_to_tray": "true"}')
    assert bridge.allowClose() is False


def test_restore_settings_applies_all(bridge, mock_agent, tmp_path):
    store = MemoryStore(tmp_path / "nova.db")
    store.set_setting("temperature", "0.1")
    store.set_setting("num_ctx", "16384")
    store.set_setting("system_prompt", "Sys")
    bridge.store = store

    bridge.restore_settings()

    assert mock_agent.options == {"temperature": 0.1, "num_ctx": 16384}
    assert mock_agent.context.system_prompt == "Sys"


@pytest.mark.asyncio
async def test_streaming_signal_and_stop_generation(bridge, mock_agent):
    async def endless_send(text):
        yield StreamChunk(token="x", done=False)
        await asyncio.sleep(30)

    mock_agent.send = endless_send

    states = []
    bridge.streamingChanged.connect(lambda s: states.append(s))

    task = asyncio.ensure_future(bridge.sendMessage("Hi"))
    await asyncio.sleep(0.01)
    bridge.stopGeneration()
    await asyncio.sleep(0.01)

    assert task.done()
    assert bridge._send_task is None
    assert states[0] is True
    assert states[-1] is False


@pytest.mark.asyncio
async def test_regenerate_reruns_last_prompt(bridge, mock_agent):
    added = []
    bridge.messageAdded.connect(
        lambda role, content, streaming: added.append(role)
    )
    bridge._last_user_prompt = "Hello again"

    bridge.regenerateLast()
    await asyncio.sleep(0.02)

    assert added[0] == "regenerate"
    assert "user" in added
    assert "assistant" in added


def test_regenerate_noop_without_last_prompt(bridge, mock_agent):
    bridge._last_user_prompt = ""
    bridge.regenerateLast()
    mock_agent.context.pop_last_assistant.assert_not_called()


def test_regenerate_noop_while_streaming(bridge, mock_agent):
    bridge._last_user_prompt = "Hi"
    task = MagicMock()
    task.done.return_value = False
    bridge._send_task = task
    bridge.regenerateLast()
    mock_agent.context.pop_last_assistant.assert_not_called()


@pytest.mark.asyncio
async def test_model_info_after_list_models(mock_agent):
    from unittest.mock import AsyncMock

    info = SimpleNamespace(
        name="qwen3:8b",
        parameter_size="8.2B",
        quantization="Q4_K_M",
        context_length=40960,
        size=5225388164,
    )
    mock_agent.provider.list_models = AsyncMock(return_value=[info])
    mock_agent.model = "qwen3:8b"
    bridge = ChatBridge(agent=mock_agent)

    await bridge.listModels()
    data = json.loads(bridge.modelInfo())

    assert data["name"] == "qwen3:8b"
    assert data["parameter_size"] == "8.2B"
    assert data["quantization"] == "Q4_K_M"
    assert data["context_length"] == 40960


def test_model_info_empty_without_cache(bridge, mock_agent):
    mock_agent.model = "unknown"
    assert json.loads(bridge.modelInfo()) == {}


@pytest.mark.asyncio
async def test_refresh_metrics_emits_gpu_memory_cpu(mock_agent):
    from nova.security.permissions import PermissionSystem
    from nova.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register_linux_tools()
    registry.permissions = PermissionSystem()
    mock_agent.registry = registry
    bridge = ChatBridge(agent=mock_agent)

    got = []
    bridge.metricsUpdated.connect(lambda j: got.append(json.loads(j)))

    await bridge.refreshMetrics()

    assert got
    data = got[0]
    assert "gpu" in data and "memory" in data and "cpu" in data
    assert "vram_used_mib" in data["gpu"]
    assert "total_bytes" in data["memory"]
    assert "cores" in data["cpu"]


@pytest.mark.asyncio
async def test_response_metrics_emitted(mock_agent):
    async def stats_send(text):
        yield StreamChunk(
            token="Hi", done=False, prompt_eval_count=10,
            prompt_eval_duration_ns=100_000_000,
        )
        yield StreamChunk(token=" there", done=False, eval_count=3)
        yield StreamChunk(
            token="", done=True, eval_count=3, eval_duration_ns=100_000_000,
            total_duration_ns=500_000_000,
        )

    mock_agent.send = stats_send
    mock_agent.model = "qwen3:8b"
    bridge = ChatBridge(agent=mock_agent)

    got = []
    bridge.responseMetrics.connect(lambda j: got.append(json.loads(j)))

    await bridge.sendMessage("Hi")

    assert got
    stats = got[0]
    assert stats["tokens"] == 3
    assert stats["prompt_tokens"] == 10
    assert stats["model"] == "qwen3:8b"
    assert stats["tools"] == 0
    assert stats["eval_ns"] == 100_000_000


@pytest.mark.asyncio
async def test_response_metrics_not_emitted_on_empty_reply(mock_agent):
    async def empty_send(text):
        yield StreamChunk(token="", done=True)

    mock_agent.send = empty_send
    bridge = ChatBridge(agent=mock_agent)

    got = []
    bridge.responseMetrics.connect(lambda j: got.append(j))

    await bridge.sendMessage("Hi")

    assert got == []


def test_skills_returns_json_without_loader(bridge, mock_agent):
    assert json.loads(bridge.skills()) == []


def test_skills_returns_loader_json(mock_agent):
    loader = MagicMock()
    loader.to_json.return_value = '[{"name": "example", "enabled": true}]'
    bridge = ChatBridge(agent=mock_agent, skill_loader=loader)

    assert json.loads(bridge.skills()) == [{"name": "example", "enabled": True}]


def test_toggle_skill_enables_and_reports(bridge, mock_agent):
    loader = MagicMock()
    loader.set_enabled.return_value = True
    bridge = ChatBridge(agent=mock_agent, skill_loader=loader)

    statuses = []
    bridge.statusChanged.connect(lambda s: statuses.append(s))

    bridge.toggleSkill("example", True)

    loader.set_enabled.assert_called_once_with("example", True)
    assert any("enabled" in s for s in statuses)


def test_toggle_skill_not_found_reports(bridge, mock_agent):
    loader = MagicMock()
    loader.set_enabled.return_value = False
    bridge = ChatBridge(agent=mock_agent, skill_loader=loader)

    statuses = []
    bridge.statusChanged.connect(lambda s: statuses.append(s))

    bridge.toggleSkill("nope", True)

    assert any("not found" in s for s in statuses)


@pytest.mark.asyncio
async def test_respond_permission_remember_grants_and_persists(bridge, tmp_path):
    from nova.memory.store import MemoryStore
    from nova.security.permissions import PermissionSystem

    store = MemoryStore(tmp_path / "nova.db")
    perms = PermissionSystem()
    bridge.agent.registry.permissions = perms
    bridge.store = store

    task = asyncio.get_running_loop().create_future()
    bridge._pending_permission = task
    bridge._grant_tool = make_tool()

    bridge.respondPermission(True, remember=True)

    assert perms.has_grant("test.perm")
    assert store.get_setting("perm_grant_test.perm") == "true"
    assert task.result() is True


def test_restore_settings_restores_grants(bridge, tmp_path):
    from nova.memory.store import MemoryStore
    from nova.security.permissions import PermissionSystem

    store = MemoryStore(tmp_path / "nova.db")
    store.set_setting("perm_grant_test.perm", "true")
    bridge.store = store
    bridge.agent.registry.permissions = PermissionSystem()

    bridge.restore_settings()

    assert bridge.agent.registry.permissions.has_grant("test.perm")


def test_reduce_motion_in_default_settings():
    from nova.ui.bridge import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["reduce_motion"] == "false"

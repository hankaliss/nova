"""Tests for the JSON-RPC backend (NovaRpc / Server)."""

import asyncio
import json
import os
import sys
from io import BytesIO

import pytest

from nova.rpc import NovaRpc, Server, Transport


class PipeTransport:
    """In-memory transport capturing all sent messages."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._inbox: asyncio.Queue = asyncio.Queue()

    def send(self, message: dict) -> None:
        self.sent.append(message)

    def deliver(self, message: dict) -> None:
        self._inbox.put_nowait(message)

    async def read_line(self) -> str:
        message = await self._inbox.get()
        return json.dumps(message)


class FakeStore:
    """Minimal MemoryStore stand-in for RPC tests."""

    def __init__(self) -> None:
        self._settings: dict[str, str] = {}
        self._messages: list = []
        self._conversations: list = []

    def get_setting(self, key, default=None):
        return self._settings.get(key, default)

    def set_setting(self, key, value):
        self._settings[key] = value

    def list_settings(self):
        return list(self._settings.items())

    def create_conversation(self):
        self._conversations.append({"id": len(self._conversations) + 1})
        return self._conversations[-1]["id"]

    def list_conversations(self):
        return self._conversations

    def get_messages(self, conversation_id):
        return []

    def add_message(self, conversation_id, role, content):
        self._messages.append((conversation_id, role, content))

    def delete_last_message(self, conversation_id):
        if self._messages:
            self._messages.pop()

    def delete_conversation(self, conversation_id):
        self._conversations = [
            c for c in self._conversations if c["id"] != conversation_id
        ]


@pytest.fixture
def rpc(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.argv", ["nova-rpc", "--db", str(tmp_path / "nova.db")])
    transport = PipeTransport()
    rpc = NovaRpc(transport)
    return rpc, transport


@pytest.mark.asyncio
async def test_setup_and_settings(rpc):
    nova, transport = rpc
    await nova.setup()

    settings = json.loads(nova.getSettings())
    assert settings["reduce_motion"] == "false"
    assert settings["close_to_tray"] == "true"


@pytest.mark.asyncio
async def test_allow_close_follows_setting(rpc):
    nova, transport = rpc
    await nova.setup()

    assert nova.allowClose() is False
    nova.saveSettings(json.dumps({"close_to_tray": "false"}))
    assert nova.allowClose() is True


@pytest.mark.asyncio
async def test_send_message_emits_events(rpc, monkeypatch):
    nova, transport = rpc
    await nova.setup()

    async def fake_send(text):
        yield SimpleNamespace(token="Hello", prompt_eval_count=5, eval_count=2,
                              tool_calls=[], prompt_eval_duration_ns=1_000_000,
                              eval_duration_ns=2_000_000, total_duration_ns=3_000_000)

    agent = SimpleNamespace(
        model="qwen3:8b",
        context=SimpleNamespace(add_user=lambda c: None, add_assistant=lambda c: None,
                                pop_last_assistant=lambda: None, pop_last_user=lambda: None,
                                system_prompt="", clear=lambda: None),
        provider=SimpleNamespace(list_models=async_list_models),
        registry=SimpleNamespace(permissions=SimpleNamespace(
            auto_approve_writes=False, grant=lambda p: None)),
        options={},
        tool_callback=None,
    )
    nova._agent = agent
    agent.send = fake_send

    await nova.sendMessage("hi there")

    events = [m for m in transport.sent if "event" in m]
    assert any(e["event"] == "messageAdded" and e["params"][0] == "user" for e in events)
    assert any(e["event"] == "messageAdded" and e["params"][2] is True for e in events)
    assert any(e["event"] == "streamUpdated" and "Hello" in e["params"][0] for e in events)
    assert any(e["event"] == "streamingChanged" and e["params"] == [True] for e in events)
    assert any(e["event"] == "streamCompleted" for e in events)
    assert any(e["event"] == "responseMetrics" for e in events)


@pytest.mark.asyncio
async def test_stop_generation_cancels_inflight_dispatch(rpc):
    """stopGeneration must be dispatchable while sendMessage is streaming
    (each request runs in its own task; the read loop must not block)."""
    nova, transport = rpc
    await nova.setup()

    async def slow_send(text):
        for i in range(50):
            yield SimpleNamespace(token=f"tok{i}", prompt_eval_count=0,
                                  eval_count=0, tool_calls=[],
                                  prompt_eval_duration_ns=None,
                                  eval_duration_ns=None, total_duration_ns=None)
            await asyncio.sleep(0.05)

    agent = SimpleNamespace(
        model="qwen3:8b",
        context=SimpleNamespace(add_user=lambda c: None, add_assistant=lambda c: None,
                                pop_last_assistant=lambda: None, pop_last_user=lambda: None,
                                system_prompt="", clear=lambda: None),
        provider=SimpleNamespace(list_models=async_list_models),
        registry=SimpleNamespace(permissions=SimpleNamespace(
            auto_approve_writes=False, grant=lambda p: None)),
        options={},
        tool_callback=None,
    )
    nova._agent = agent
    agent.send = slow_send

    server = Server(nova, transport)
    for name in ("sendMessage", "stopGeneration"):
        server.register(name)(getattr(nova, name))

    send_task = asyncio.ensure_future(server._dispatch(
        {"id": 1, "method": "sendMessage", "params": ["long essay"]}))
    await asyncio.sleep(0.2)  # sendMessage is streaming now
    assert nova._send_task is not None and not nova._send_task.done()

    await server._dispatch({"id": 2, "method": "stopGeneration", "params": {}})

    await asyncio.wait_for(send_task, timeout=2)  # cancelled dispatch ends
    events = [m for m in transport.sent if m.get("event") == "statusChanged"]
    assert any("stopped" in e["params"][0] for e in events)
    assert any(m.get("event") == "streamCompleted" for m in transport.sent)
    assert any(m.get("event") == "streamingChanged" and m["params"] == [False]
               for m in transport.sent)


@pytest.mark.asyncio
async def test_switch_conversation_replays_after_switched_event(rpc):
    """conversationSwitched must be emitted BEFORE the replayed messageAdded
    events, otherwise the UI (which clears on conversationSwitched) wipes the
    just-replayed chat. Regression test for the lost-chat-on-switch bug."""
    nova, transport = rpc
    await nova.setup()
    c1 = nova._store.create_conversation()
    c2 = nova._store.create_conversation()
    from nova.llm.models import Role

    nova._store.add_message(c2, Role.USER, "second conv question")
    nova._store.add_message(c2, Role.ASSISTANT, "second conv answer")
    nova._conversation_id = c1
    agent = SimpleNamespace(
        reset=lambda: None,
        context=SimpleNamespace(add_user=lambda c: None,
                                add_assistant=lambda c: None,
                                pop_last_assistant=lambda: None,
                                pop_last_user=lambda: None,
                                system_prompt="", clear=lambda: None),
    )
    nova._agent = agent

    transport.sent.clear()
    nova.switchConversation(c2)

    events = [m for m in transport.sent if "event" in m]
    switched_idx = next(
        i for i, e in enumerate(events) if e["event"] == "conversationSwitched"
    )
    replay = [e for e in events if e["event"] == "messageAdded"]
    assert len(replay) == 2
    assert replay[0]["params"] == ["user", "second conv question", False]
    assert replay[1]["params"] == ["assistant", "second conv answer", False]
    assert all(
        i > switched_idx for i, e in enumerate(events) if e["event"] == "messageAdded"
    ), "messageAdded replay must come AFTER conversationSwitched"
    assert nova._conversation_id == c2


@pytest.mark.asyncio
async def test_clear_history_noop_on_empty_conversation(rpc):
    """clearHistory on an empty conversation resets agent without creating a new one."""
    nova, transport = rpc
    await nova.setup()
    c1 = nova._store.create_conversation()
    nova._conversation_id = c1
    before = nova._store.list_conversations()

    transport.sent.clear()
    nova.clearHistory()

    # Active conversation unchanged
    assert nova._conversation_id == c1
    # No new conversation created
    events = [m for m in transport.sent if "event" in m]
    lists = [e for e in events if e["event"] == "conversation_list"]
    assert lists
    convs = lists[-1]["params"][0]
    assert len(convs) == len(before)


@pytest.mark.asyncio
async def test_clear_history_creates_new_when_has_messages(rpc):
    """clearHistory on a conversation with messages creates a new one."""
    nova, transport = rpc
    await nova.setup()
    c1 = nova._store.create_conversation()
    nova._conversation_id = c1
    from nova.llm.models import Role

    nova._store.add_message(c1, Role.USER, "hello")
    before = nova._store.list_conversations()

    transport.sent.clear()
    nova.clearHistory()

    # New conversation created, old one preserved
    assert nova._conversation_id != c1
    events = [m for m in transport.sent if "event" in m]
    lists = [e for e in events if e["event"] == "conversation_list"]
    assert lists
    convs = lists[-1]["params"][0]
    assert len(convs) == len(before) + 1
    old_ids = {c["id"] for c in before}
    assert any(c["id"] in old_ids for c in convs)


@pytest.mark.asyncio
async def test_setup_does_not_create_empty_conversation(rpc):
    """setup() must not leak a new conversation: every app start previously
    created an empty conversation and made it active, so the chat always
    opened empty. load_history() picks the last real conversation instead."""
    nova, transport = rpc
    await nova.setup()
    assert nova._conversation_id is None
    assert nova._store.list_conversations() == []

    c1 = nova._store.create_conversation()
    nova.load_history()
    assert nova._conversation_id == c1
    assert len(nova._store.list_conversations()) == 1


@pytest.mark.asyncio
async def test_dispatch_roundtrip(rpc):
    nova, transport = rpc
    server = Server(nova, transport)
    for name in ("getSettings", "allowClose"):
        server.register(name)(getattr(nova, name))

    await server._dispatch({"id": 1, "method": "getSettings", "params": []})

    response = transport.sent[-1]
    assert response["id"] == 1
    assert "reduce_motion" in json.loads(response["result"])


@pytest.mark.asyncio
async def test_dispatch_matches_dict_params_by_name(rpc):
    """Dict params must be matched to method parameters by name, so
    multi-key objects (settings) don't explode into positional args."""
    nova, transport = rpc
    await nova.setup()
    server = Server(nova, transport)
    for name in ("saveSettings", "toggleSkill"):
        server.register(name)(getattr(nova, name))

    await server._dispatch({"id": 2, "method": "saveSettings", "params": {
        "settings_json": json.dumps({"temperature": "0.5", "num_ctx": "8192"}),
    }})

    response = transport.sent[-1]
    assert response["id"] == 2
    assert response["result"] is None
    assert nova._settings["temperature"] == "0.5"
    assert nova._settings["num_ctx"] == "8192"

    await server._dispatch({"id": 3, "method": "toggleSkill", "params": {
        "name": "system_metrics", "enabled": False,
    }})
    response = transport.sent[-1]
    assert response["id"] == 3
    assert "error" not in response  # skill loaded from real repo, dispatch matched args
    events = [m for m in transport.sent if m.get("event") == "statusChanged"]
    assert any("disabled" in e["params"][0] for e in events)


@pytest.mark.asyncio
async def test_unknown_method_error(rpc):
    nova, transport = rpc
    server = Server(nova, transport)
    await server._dispatch({"id": 7, "method": "nope"})

    assert transport.sent[-1]["error"]["code"] == -32601


def test_nova_rpc_module_runs():
    """The module imports and exposes its entry point."""
    import nova.rpc

    assert callable(nova.rpc.main)


class SimpleNamespace:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


async def async_list_models():
    return []


def test_transport_encodes_lines():
    out = BytesIO()
    writer = SimpleNamespace(write=lambda b: out.write(b), flush=lambda: None)
    transport = Transport(None, writer)
    transport.send({"event": "test", "params": [1, "x"]})
    assert out.getvalue() == b'{"event": "test", "params": [1, "x"]}\n'
"""Tests for the JSON-RPC backend (NovaRpc / Server)."""

import asyncio
import json
from io import BytesIO
from pathlib import Path

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

    def delete_last_turn(self, conversation_id):
        return 0

    def delete_conversation(self, conversation_id):
        self._conversations = [
            c for c in self._conversations if c["id"] != conversation_id
        ]


class FakeShortcut:
    """Stands in for nova.kde.shortcut.GlobalShortcut (kglobalaccel)."""

    instances: list["FakeShortcut"] = []
    available = True

    def __init__(self, on_pressed):
        self.on_pressed = on_pressed
        self.keys: list[str] = []
        FakeShortcut.instances.append(self)

    async def start(self):
        return FakeShortcut.available

    async def set_keys(self, text):
        from nova.kde.shortcut import ShortcutTaken, parse_shortcut

        if text == "Meta+W":
            raise ShortcutTaken("Meta+W is already used by KWin: Toggle Overview")
        if text:
            parse_shortcut(text)
        self.keys.append(text)
        return text

    async def stop(self):
        pass


@pytest.fixture
def rpc(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.argv", ["nova-rpc", "--db", str(tmp_path / "nova.db")])
    # Never read or write the real ~/.local/share/nova/skills.
    monkeypatch.setattr("nova.rpc.core.USER_SKILLS_DIR", tmp_path / "user_skills")
    # ...nor the user's starship prompt config.
    monkeypatch.setattr("nova.rpc.core._starship_config", lambda: None)
    # ...nor start (or auto-create) the user's MCP servers from
    # ~/.config/nova/mcp.json.
    monkeypatch.setattr("nova.mcp.manager.default_config_path", lambda: tmp_path / "mcp.json")
    monkeypatch.setattr("nova.mcp.manager.auto_configure_github", lambda **kw: None)
    # ...nor register a global shortcut with the user's KDE session.
    monkeypatch.setattr("nova.rpc.shortcut.GlobalShortcut", FakeShortcut)
    # ...nor load models in the real Ollama to fit the context size.
    from nova.llm.context_fit import FitResult

    async def fake_fit(provider, model, free_vram):
        return FitResult(num_ctx=12288, on_gpu=True)

    async def no_placement(self, model):
        return None

    monkeypatch.setattr("nova.rpc.models.fit_context", fake_fit)
    monkeypatch.setattr("nova.llm.ollama.OllamaProvider.placement", no_placement)
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
    user_added = [e for e in events if e["event"] == "messageAdded" and e["params"][0] == "user"]
    assistant_mini = [e for e in events if e["event"] == "messageAdded" and e["params"][1] == ""]
    assert user_added and isinstance(user_added[0]["params"][3], int)
    assert assistant_mini  # the empty streaming placeholder
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
    assert replay[0]["params"][:3] == ["user", "second conv question", False]
    assert isinstance(replay[0]["params"][3], int)
    assert replay[1]["params"][:3] == ["assistant", "second conv answer", False]
    assert isinstance(replay[1]["params"][3], int)
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
        "name": "system_doctor", "enabled": False,
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


@pytest.mark.asyncio
async def test_list_models_emits_details(rpc):
    """listModels must emit per-model capability metadata (model_details)
    alongside the legacy name-only modelsListed event."""
    from nova.llm.models import ModelInfo

    nova, transport = rpc
    await nova.setup()

    async def _models():
        return [
            ModelInfo(
                name="qwen3:8b",
                size=5_225_388_164,
                parameter_size="8.2B",
                quantization="Q4_K_M",
                context_length=40960,
                capabilities=["completion", "tools"],
            ),
            ModelInfo(
                name="qwen2.5-coder:7b",
                size=4_683_087_561,
                parameter_size="7.6B",
                quantization="Q4_K_M",
                context_length=32768,
                capabilities=["completion", "tools", "insert"],
            ),
        ]

    nova._provider = SimpleNamespace(list_models=_models)
    nova.listModels()
    await asyncio.sleep(0)

    events = [m for m in transport.sent if "event" in m]
    assert any(e["event"] == "modelsListed" for e in events)
    details = [e for e in events if e["event"] == "model_details"]
    assert len(details) == 1
    listed = details[0]["params"][0]
    assert [m["name"] for m in listed] == ["qwen3:8b", "qwen2.5-coder:7b"]
    assert listed[0]["parameter_size"] == "8.2B"
    assert listed[0]["quantization"] == "Q4_K_M"
    assert listed[0]["context_length"] == 40960
    assert listed[0]["capabilities"] == ["completion", "tools"]
    assert listed[0]["size"] == 5_225_388_164


@pytest.mark.asyncio
async def test_get_settings_reports_active_prompt_source(rpc, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    nova, transport = rpc
    await nova.setup()

    bundled = (Path(__file__).resolve().parent.parent / "agent.md").read_text()
    settings = json.loads(nova.getSettings())
    assert settings["system_prompt"] == bundled.strip()
    assert "bundled file" in settings["system_prompt_source"]


@pytest.mark.asyncio
async def test_save_settings_writes_user_agent_md(rpc, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    nova, transport = rpc
    await nova.setup()

    nova.saveSettings(json.dumps({"system_prompt": "You are TestBot."}))
    user_file = tmp_path / ".config" / "nova" / "agent.md"
    assert user_file.read_text().strip() == "You are TestBot."
    assert nova._agent.context.system_prompt == "You are TestBot."

    payload = json.loads(nova.getSettings())
    assert payload["system_prompt"] == "You are TestBot."
    assert "user file" in payload["system_prompt_source"]


@pytest.mark.asyncio
async def test_save_settings_clearing_resets_to_bundled(rpc, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    nova, transport = rpc
    await nova.setup()

    nova.saveSettings(json.dumps({"system_prompt": "Temp"}))
    user_file = tmp_path / ".config" / "nova" / "agent.md"
    assert user_file.exists()

    nova.saveSettings(json.dumps({"system_prompt": ""}))
    assert not user_file.exists()
    bundled = (Path(__file__).resolve().parent.parent / "agent.md").read_text()
    assert nova._agent.context.system_prompt == bundled.strip()


@pytest.mark.asyncio
async def test_save_settings_other_keys_keep_user_prompt(rpc, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    nova, transport = rpc
    await nova.setup()

    nova.saveSettings(json.dumps({"system_prompt": "Persistent"}))
    nova.saveSettings(json.dumps({"temperature": "0.2"}))
    user_file = tmp_path / ".config" / "nova" / "agent.md"
    assert user_file.read_text().strip() == "Persistent"
    assert nova._settings.get("system_prompt_source") is None


@pytest.mark.asyncio
async def test_save_settings_always_emits_settings(rpc, tmp_path, monkeypatch):
    # The Electron main process applies always_on_top from this event.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    nova, transport = rpc
    await nova.setup()
    transport.sent.clear()

    nova.saveSettings(json.dumps({"always_on_top": "true"}))
    events = [m for m in transport.sent if m.get("event") == "settings"]
    assert events and events[-1]["params"][0]["always_on_top"] == "true"


def test_transport_encodes_lines():
    out = BytesIO()
    writer = SimpleNamespace(write=lambda b: out.write(b), flush=lambda: None)
    transport = Transport(None, writer)
    transport.send({"event": "test", "params": [1, "x"]})
    assert out.getvalue() == b'{"event": "test", "params": [1, "x"]}\n'


# ---- Multiple-choice questions (nova.presentChoices) ----------------------


@pytest.mark.asyncio
async def test_request_choices_emits_event_and_select_resolves(rpc):
    nova, transport = rpc
    await nova.setup()

    task = asyncio.ensure_future(nova.request_choices("Pick one", ["A", "B", "C"]))
    await asyncio.sleep(0)  # let the coroutine emit and block

    raw = [m for m in transport.sent if m.get("event") == "choicesRequested"]
    assert raw, "choicesRequested event not emitted"
    assert raw[0]["params"][0] == "Pick one"
    assert json.loads(raw[0]["params"][1]) == ["A", "B", "C"]

    aliased = [m for m in transport.sent if m.get("event") == "choices"]
    assert aliased
    assert aliased[0]["params"][0]["question"] == "Pick one"
    assert aliased[0]["params"][0]["options"] == ["A", "B", "C"]

    assert nova.selectChoice(1) == "B"
    assert await task == "B"


@pytest.mark.asyncio
async def test_select_choice_without_pending_is_noop(rpc):
    nova, transport = rpc
    await nova.setup()
    assert nova.selectChoice(0) == ""
    assert nova.selectChoice(99) == ""


@pytest.mark.asyncio
async def test_request_choices_validates_input(rpc):
    nova, transport = rpc
    await nova.setup()

    with pytest.raises(ValueError):
        await nova.request_choices("", ["A", "B"])
    with pytest.raises(ValueError):
        await nova.request_choices("Q", ["only one"])

    first = asyncio.ensure_future(nova.request_choices("First", ["A", "B"]))
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError):
        await nova.request_choices("Second", ["C", "D"])

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert nova._pending_choice is None


@pytest.mark.asyncio
async def test_choice_out_of_range_resolves_empty(rpc):
    nova, transport = rpc
    await nova.setup()

    task = asyncio.ensure_future(nova.request_choices("Pick", ["A", "B"]))
    await asyncio.sleep(0)
    assert nova.selectChoice(5) == ""
    assert await task == ""

# ---- Skill save fallback (createSkill RPC) ------------------------------


async def _make_skill_loader(nova, tmp_path):
    """Point the RPC at a throwaway skills dir (skill-maker tools are already
    registered from setup(); only the loader target changes)."""
    from nova.skills.loader import SkillLoader

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    nova._skill_loader = SkillLoader(
        tmp_path / "bundled",
        registry=nova._registry,
        store=nova._store,
        user_dir=skills_dir,
    )
    # Saving is DESTRUCTIVE-gated; stand in for the user clicking Approve.
    nova._registry.permissions.approval_callback = lambda tool, args: True
    return skills_dir


def _weather_skill_texts():
    skill_md = "# Weather\n\nGets the current weather.\n"
    tools_py = (
        "from nova.tools.tool import Tool, RiskLevel\n"
        "\n"
        "def get_tools():\n"
        "    return [Tool(\n"
        "        name='weather.get', description='Get the weather',\n"
        "        parameters={'type': 'object', 'properties': {}},\n"
        "        risk=RiskLevel.READ, implementation=lambda: 'sunny',\n"
        "    )]\n"
    )
    return skill_md, tools_py


@pytest.mark.asyncio
async def test_create_skill_rpc_writes_files(rpc, tmp_path):
    nova, transport = rpc
    await nova.setup()
    skills_dir = await _make_skill_loader(nova, tmp_path)

    skill_md, tools_py = _weather_skill_texts()
    result = await nova.createSkill("", skill_md, tools_py)

    assert "Created skill 'weather'" in result
    assert (skills_dir / "weather" / "SKILL.md").read_text().startswith("# Weather")
    assert (skills_dir / "weather" / "tools.py").exists()
    assert nova._skill_loader.discover()[0].origin == "user"
    assert any(m.get("event") == "skill_list" for m in transport.sent)


@pytest.mark.asyncio
async def test_create_skill_rpc_rejects_invalid_name(rpc, tmp_path):
    nova, transport = rpc
    await nova.setup()
    await _make_skill_loader(nova, tmp_path)

    skill_md, tools_py = _weather_skill_texts()
    with pytest.raises(ValueError):
        await nova.createSkill("Bad Skill!", skill_md, tools_py)
    with pytest.raises(ValueError):
        await nova.createSkill("1abc", skill_md, tools_py)


@pytest.mark.asyncio
async def test_create_skill_rpc_aligns_tool_prefixes(rpc, tmp_path):
    nova, transport = rpc
    await nova.setup()
    skills_dir = await _make_skill_loader(nova, tmp_path)

    # Model wrote a heading/path name that disagrees with the tool prefix —
    # the code says system_updates.check but the user picked pacman_uptade
    # (typo heading). createSkill must rewrite the prefix so validation passes.
    skill_md, _ = _weather_skill_texts()
    tools_py = (
        "from nova.tools.tool import Tool, RiskLevel\n"
        '\ndef get_tools():\n    return [Tool(\n'
        "        name='system_updates.check',\n"
        '        description="Checks for available system updates.",\n'
        "        parameters={},\n"
        "        risk=RiskLevel.READ,\n"
        '        implementation=lambda: subprocess.run(["pacman", "-Qu"]),\n'
        "    )]\n"
    )
    result = await nova.createSkill("pacman_uptade", skill_md, tools_py)

    assert "Created skill 'pacman_uptade'" in result
    written = (skills_dir / "pacman_uptade" / "tools.py").read_text()
    assert "name='pacman_uptade.check'" in written
    assert "system_updates.check" not in written
    assert nova._registry.get("pacman_uptade.check") is not None


@pytest.mark.asyncio
async def test_create_skill_rpc_second_save_updates(rpc, tmp_path):
    nova, transport = rpc
    await nova.setup()
    await _make_skill_loader(nova, tmp_path)

    skill_md, tools_py = _weather_skill_texts()
    first = await nova.createSkill("", skill_md, tools_py)
    assert "Created skill 'weather'" in first

    # Re-saving the same skill (e.g. to fix the version whose smoke test
    # failed) must update it — overwriting files and re-registering once —
    # rather than erroring with "Tool ... is already registered".
    second = await nova.createSkill("", skill_md, tools_py)
    assert "Updated skill 'weather'" in second
    assert "already registered" not in second
    registered = [t.name for t in nova._registry.list_tools()].count("weather.get")
    assert registered == 1


@pytest.mark.asyncio
async def test_setup_registers_persistent_shell_tools(rpc):
    nova, transport = rpc
    await nova.setup()
    names = [t.name for t in nova._registry.list_tools()]
    assert "shell.execute" in names
    assert "shell.session_info" in names
    assert "shell.reset" in names
    assert nova._shell_session is not None
    nova.quit()
    assert nova._shell_session.alive is False


def _term_outputs(transport) -> str:
    """Concatenate all term_output payloads seen so far (base64)."""
    import base64

    return "".join(
        base64.b64decode(m["params"][0]).decode("utf-8", "replace")
        for m in transport.sent
        if m.get("event") == "term_output"
    )


@pytest.mark.asyncio
async def test_terminal_open_streams_replay_and_live_output(rpc):
    nova, transport = rpc
    await nova.setup()
    await nova.terminalOpen()
    assert nova._shell_session._subscribers, "terminalOpen must subscribe"

    # Replay: opening the panel must have seeded at least a prompt.
    for _ in range(100):
        if _term_outputs(transport):
            break
        await asyncio.sleep(0.05)
    assert _term_outputs(transport)

    # Live output: anything the shared session prints must arrive as events.
    result = await nova._shell_session.run("echo TERM_LIVE")
    assert result["returncode"] == 0
    for _ in range(100):
        if "TERM_LIVE" in _term_outputs(transport):
            break
        await asyncio.sleep(0.05)
    assert "TERM_LIVE" in _term_outputs(transport)

    nova.terminalClose()
    assert nova._shell_session.alive is True  # session survives close
    nova.quit()


@pytest.mark.asyncio
async def test_terminal_input_round_trip(rpc):
    nova, transport = rpc
    await nova.setup()
    await nova.terminalOpen()
    nova.terminalInput("echo TERM_INPUT; echo TERM_DONE\n")
    for _ in range(100):
        if "TERM_DONE" in _term_outputs(transport):
            break
        await asyncio.sleep(0.05)
    assert "TERM_DONE" in _term_outputs(transport)
    nova.terminalClose()
    nova.quit()


@pytest.mark.asyncio
async def test_terminal_resize(rpc):
    nova, transport = rpc
    await nova.setup()
    await nova.terminalOpen()
    nova.terminalResize(99, 21)
    result = await nova._shell_session.run("stty size")
    assert result["returncode"] == 0
    assert result["output"].strip() == "21 99"
    nova.terminalClose()
    nova.quit()


@pytest.mark.asyncio
async def test_create_skill_rpc_denied_writes_nothing(rpc, tmp_path):
    nova, transport = rpc
    await nova.setup()
    skills_dir = await _make_skill_loader(nova, tmp_path)
    seen = []

    def deny(tool, args):
        seen.append((tool.name, args["tools_py"]))
        return False

    nova._registry.permissions.approval_callback = deny
    skill_md, tools_py = _weather_skill_texts()
    with pytest.raises(Exception, match="DESTRUCTIVE"):
        await nova.createSkill("", skill_md, tools_py)
    assert seen and seen[0][0] == "nova.skill.create"
    assert "def get_tools" in seen[0][1]
    assert not (skills_dir / "weather").exists()


@pytest.mark.asyncio
async def test_respond_permission_never_remembers_destructive(rpc):
    nova, transport = rpc
    await nova.setup()
    tool = nova._registry.get("nova.skill.delete")
    task = asyncio.ensure_future(nova._request_permission(tool, {"name": "x"}))
    await asyncio.sleep(0)
    nova.respondPermission(True, True)
    assert await task is True
    assert nova._store.get_setting("perm_grant_skill_maker.delete") is None
    assert not nova._registry.permissions.has_grant("skill_maker.delete")


@pytest.mark.asyncio
async def test_respond_permission_remembers_write(rpc):
    nova, transport = rpc
    await nova.setup()
    tool = nova._registry.get("shell.execute")
    task = asyncio.ensure_future(nova._request_permission(tool, {"command": "ls"}))
    await asyncio.sleep(0)
    nova.respondPermission(True, True)
    assert await task is True
    assert nova._store.get_setting("perm_grant_shell.execute") == "true"



# ---- openPath (renderer-supplied paths) -----------------------------------


@pytest.fixture
def opened(monkeypatch):
    calls = []
    monkeypatch.setattr("subprocess.Popen", lambda argv, *a, **k: calls.append(argv))
    return calls


def test_open_path_opens_plain_files_and_folders(rpc, tmp_path, opened):
    nova, _ = rpc
    doc = tmp_path / "report.txt"
    doc.write_text("hi")
    assert nova.openPath(str(doc)) is True
    assert nova.openPath(str(tmp_path)) is True
    assert opened == [["xdg-open", str(doc)], ["xdg-open", str(tmp_path)]]


def test_open_path_refuses_things_that_would_run(rpc, tmp_path, opened):
    nova, _ = rpc
    script = tmp_path / "run.sh"
    script.write_text("#!/bin/sh\necho pwned\n")
    script.chmod(0o755)
    launcher = tmp_path / "evil.desktop"
    launcher.write_text("[Desktop Entry]\nExec=sh -c 'echo pwned'\n")
    assert nova.openPath(str(script)) is False
    assert nova.openPath(str(launcher)) is False
    assert nova.openPath("relative/path.txt") is False
    assert nova.openPath(str(tmp_path / "missing.txt")) is False
    assert nova.openPath("/dev/null") is False
    assert opened == []


# ---- Automatic context size (AD-028) ---------------------------------------


@pytest.mark.asyncio
async def test_auto_context_fits_selected_model_and_remembers(rpc):
    nova, transport = rpc
    await nova.setup()
    nova.selectModel("qwen3:4b")
    await nova._fit_task
    assert nova._agent.options["num_ctx"] == 12288          # from the (fake) fit
    assert nova._store.get_setting("auto_ctx_qwen3:4b") == "12288"
    sizes = [m["params"][0] for m in transport.sent if m.get("event") == "context_size"]
    assert sizes[-1]["auto"] is True and sizes[-1]["num_ctx"] == 12288


@pytest.mark.asyncio
async def test_manual_context_overrides_auto_and_skips_fitting(rpc):
    nova, transport = rpc
    await nova.setup()
    nova.saveSettings(json.dumps({"num_ctx": "4096"}))
    assert nova._agent.options["num_ctx"] == 4096
    assert nova._fit_task is None
    nova.selectModel("qwen3:8b")
    assert nova._fit_task is None and nova._agent.options["num_ctx"] == 4096


@pytest.mark.asyncio
async def test_unrelated_settings_save_does_not_refit(rpc):
    nova, transport = rpc
    await nova.setup()
    nova.selectModel("qwen3:4b")
    first = nova._fit_task
    nova.saveSettings(json.dumps({"close_to_tray": "false"}))
    assert nova._fit_task is first


@pytest.mark.asyncio
async def test_spill_after_answer_steps_context_down(rpc, monkeypatch):
    nova, transport = rpc
    await nova.setup()
    nova.selectModel("qwen3:4b")
    await nova._fit_task

    async def spilled(self, model):
        return (6_000_000_000, 5_000_000_000)   # size, size_vram: not all on GPU

    monkeypatch.setattr("nova.llm.ollama.OllamaProvider.placement", spilled)
    await nova._check_context_spill()
    assert nova._agent.options["num_ctx"] == 8192
    assert nova._store.get_setting("auto_ctx_qwen3:4b") == "8192"



# ---- Memories ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_relevant_memories_reach_the_prompt(rpc, monkeypatch):
    nova, transport = rpc
    await nova.setup()
    nova.addMemory("The NAS is at 192.168.1.20")
    nova.addMemory("Prefers concise answers")
    seen = {}

    async def fake_send(text):
        seen["note"] = nova._agent.context.memory_note
        yield SimpleNamespace(token="ok", prompt_eval_count=1, eval_count=1, tool_calls=[],
                              prompt_eval_duration_ns=1, eval_duration_ns=1, total_duration_ns=1)

    monkeypatch.setattr(nova._agent, "send", fake_send)
    await nova.sendMessage("what is the ip of my nas")
    assert "192.168.1.20" in seen["note"]
    assert "concise" not in seen["note"]
    await nova.sendMessage("hello there")
    assert seen["note"] == ""


@pytest.mark.asyncio
async def test_memories_panel_add_list_delete(rpc):
    nova, transport = rpc
    await nova.setup()
    memory_id = nova.addMemory("Backups run on Sunday")
    assert [m["content"] for m in nova.listMemories()] == ["Backups run on Sunday"]
    assert nova.deleteMemory(memory_id) is True
    assert nova.listMemories() == []
    lists = [m for m in transport.sent if m.get("event") == "memory_list"]
    assert lists and lists[-1]["params"][0] == []


# ---- Global shortcut (AD-035) ----------------------------------------------


@pytest.mark.asyncio
async def test_shortcut_applied_from_settings(rpc):
    nova, transport = rpc
    nova._settings["toggle_shortcut"] = "Meta+Space"
    await nova.setup()
    fake = FakeShortcut.instances[-1]
    assert fake.keys == ["Meta+Space"]
    status = json.loads(nova.shortcutStatus())
    assert status == {"available": True, "shortcut": "Meta+Space", "error": ""}


@pytest.mark.asyncio
async def test_shortcut_change_on_save_and_conflict(rpc):
    nova, transport = rpc
    await nova.setup()
    fake = FakeShortcut.instances[-1]
    nova.saveSettings(json.dumps({"toggle_shortcut": "Ctrl+Alt+N"}))
    await asyncio.sleep(0)
    assert fake.keys[-1] == "Ctrl+Alt+N"
    nova.saveSettings(json.dumps({"toggle_shortcut": "Meta+W"}))
    await asyncio.sleep(0)
    status = nova._shortcut_status
    assert "KWin: Toggle Overview" in status["error"] and status["shortcut"] == ""
    assert fake.keys[-1] == ""  # the old key is released, not left bound
    events = [m for m in transport.sent if m.get("event") == "shortcut_status"]
    assert events[-1]["params"][0]["error"]


@pytest.mark.asyncio
async def test_shortcut_press_toggles_unless_recording(rpc):
    nova, transport = rpc
    await nova.setup()
    fake = FakeShortcut.instances[-1]
    fake.on_pressed()
    nova.shortcutRecording(True)
    fake.on_pressed()
    nova.shortcutRecording(False)
    fake.on_pressed()
    assert sum(1 for m in transport.sent if m.get("event") == "toggle_window") == 2


@pytest.mark.asyncio
async def test_shortcut_without_kde(rpc, monkeypatch):
    nova, transport = rpc
    monkeypatch.setattr(FakeShortcut, "available", False)
    nova._settings["toggle_shortcut"] = "Meta+Space"
    await nova.setup()
    status = nova._shortcut_status
    assert status["available"] is False and "KDE Plasma" in status["error"]
    assert FakeShortcut.instances[-1].keys == []



# ---- Waiting for a context fit (first message after a model switch) -------


def _streaming_agent(sent):
    async def fake_send(text):
        sent.append(text)
        yield SimpleNamespace(token="Hi", prompt_eval_count=0, eval_count=0, tool_calls=[],
                              prompt_eval_duration_ns=None, eval_duration_ns=None,
                              total_duration_ns=None)

    agent = SimpleNamespace(
        model="qwen3:8b",
        context=SimpleNamespace(add_user=lambda c: None, add_assistant=lambda c: None,
                               
                                system_prompt="", clear=lambda: None, memory_note=""),
        provider=SimpleNamespace(list_models=async_list_models),
        registry=SimpleNamespace(permissions=SimpleNamespace(
            auto_approve_writes=False, grant=lambda p: None)),
        options={},
        tool_callback=None,
    )
    agent.send = fake_send
    return agent


@pytest.mark.asyncio
async def test_message_shows_before_a_context_fit(rpc):
    nova, transport = rpc
    await nova.setup()
    sent = []
    nova._agent = _streaming_agent(sent)
    fit = asyncio.get_running_loop().create_future()
    nova._fit_task = fit

    task = asyncio.ensure_future(nova.sendMessage("hello"))
    await asyncio.sleep(0.05)
    names = [m.get("event") for m in transport.sent]
    assert ["user", "hello"] in [m["params"][:2] for m in transport.sent if m.get("event") == "messageAdded"]
    assert "activity_note" in names and "streamingChanged" in names
    assert sent == []  # generation waits for the fit

    fit.set_result(None)
    await asyncio.wait_for(task, 2)
    assert sent == ["hello"]
    notes = [m["params"][0] for m in transport.sent if m.get("event") == "activity_note"]
    assert notes[-1] == ""  # cleared once the fit is done


@pytest.mark.asyncio
async def test_stop_works_while_waiting_for_a_fit(rpc):
    nova, transport = rpc
    await nova.setup()
    sent = []
    nova._agent = _streaming_agent(sent)
    fit = asyncio.get_running_loop().create_future()
    nova._fit_task = fit

    task = asyncio.ensure_future(nova.sendMessage("hello"))
    await asyncio.sleep(0.05)
    nova.stopGeneration()
    await asyncio.wait_for(task, 2)
    assert sent == []
    assert any(m.get("event") == "statusChanged" and m["params"] == ["Generation stopped"]
               for m in transport.sent)
    assert not fit.cancelled()  # the fit itself carries on


@pytest.mark.asyncio
async def test_stale_skill_origin_keys_are_removed(rpc, tmp_path):
    from nova.memory.store import MemoryStore

    db = tmp_path / "nova.db"
    store = MemoryStore(str(db))
    store.set_setting("skill_origin_system_metrics", "bundled")
    store.set_setting("temperature", "0.3")
    nova, transport = rpc
    await nova.setup()
    keys = dict(nova._store.list_settings())
    assert "skill_origin_system_metrics" not in keys
    assert keys["temperature"] == "0.3"


@pytest.mark.asyncio
async def test_regenerate_drops_the_whole_last_turn(rpc):
    nova, transport = rpc
    await nova.setup()
    from nova.agent.context import ConversationContext

    ctx = ConversationContext(system_prompt="s")
    ctx.add_user("first")
    ctx.add_assistant("one")
    ctx.add_user("second")
    ctx.add_assistant_tool_call("", [])
    ctx.add_tool_result("out")
    ctx.add_assistant("two")
    sent = []

    async def fake_send_message(text):
        sent.append(text)

    nova._agent.context = ctx
    nova._last_user_prompt = "from another conversation"
    nova.sendMessage = fake_send_message
    nova.regenerateLast()
    await asyncio.sleep(0)
    assert sent == ["second"]
    assert [m.content for m in ctx.messages] == ["first", "one"]



@pytest.mark.asyncio
async def test_history_reload_strips_tool_artifacts(rpc, tmp_path):
    from nova.llm.models import Role
    from nova.memory.store import MemoryStore
    from nova.tools.report import artifact_block

    store = MemoryStore(str(tmp_path / "nova.db"))
    cid = store.create_conversation()
    store.add_message(cid, Role.USER, "check")
    store.add_message(cid, Role.ASSISTANT, "Found:" + artifact_block("R", "<html>big</html>") + "All fine.")
    nova, transport = rpc
    await nova.setup()
    nova.switchConversation(cid)
    assistant = [m.content for m in nova._agent.context.messages if m.role == Role.ASSISTANT]
    assert assistant == ["Found:\n\nAll fine."]
    # ...while the UI still gets the full reply with its artifact.
    replayed = [m["params"][1] for m in transport.sent if m.get("event") == "messageAdded" and m["params"][0] == "assistant"]
    assert "big" in replayed[-1]

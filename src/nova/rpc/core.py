"""JSON-RPC backend for the Electron frontend.

Nova's agent stack (Agent, ToolRegistry, skills, memory, permissions) is
exposed to the Electron renderer over stdio using newline-delimited JSON.

Protocol (line-based, UTF-8):

    -> {"id": 1, "method": "sendMessage", "params": ["hello"]}
    <- {"id": 1, "result": null}
    -> {"id": 2, "method": "getSettings"}
    <- {"id": 2, "result": {"model": "", ...}}
    <- {"event": "messageAdded", "params": ["user", "hello", false, 1749999999]}

Methods and events form the UI-facing API used by the Electron frontend.
"""

import asyncio
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nova.agent.agent import Agent
from nova.memory.store import DEFAULT_DB_PATH, MemoryStore
from nova.security.permissions import PermissionSystem
from nova.shell.session import ShellSession
from nova.skills.loader import SkillLoader

if TYPE_CHECKING:  # server.py imports this module
    from nova.rpc.server import Transport
from nova.tools.registry import ToolRegistry
from nova.rpc.paths import RESOURCES_DIR

# Event name mapping: backend event → (frontend event name, data transform)
# The frontend useNova hook expects different event names than the backend emits.
_ALIASES: dict[str, tuple[str, Callable[..., Any] | Any]] = {
    "streamUpdated": ("chunk", lambda token: {"content": token}),
    "streamCompleted": ("reply_done", {}),
    "streamingChanged": ("streaming", lambda val: val),
    "toolStarted": ("tool_progress", lambda name, args: {
        "name": name, "args": args, "status": "running",
        "duration_ms": 0, "error": "", "output_path": None,
    }),
    "toolFinished": ("tool_progress", lambda name, ok, dur, err: {
        "name": name, "status": "ok" if ok else "fail",
        "duration_ms": dur, "error": err, "output_path": None,
    }),
    "statusChanged": ("status", lambda text: {"ok": not text.startswith("Error")}),
    "modelsListed": ("model_list", lambda names: names),
    "responseMetrics": ("response", lambda json_str: __import__("json").loads(json_str)),
    "permissionRequested": ("permission", lambda name, risk, perm, args: {
        "tool": name, "risk": risk, "permission": perm, "args": args,
    }),
    "choicesRequested": ("choices", lambda question, options_json: {
        "question": question, "options": json.loads(options_json),
    }),
    "metricsUpdated": ("system", lambda json_str: __import__("json").loads(json_str)),
}

USER_SKILLS_DIR = DEFAULT_DB_PATH.parent / "skills"


def _starship_config() -> Path | None:
    """Starship config for the shared shell's prompt, if the user set one.

    ``$NOVA_STARSHIP_CONFIG`` wins, else ``~/.config/nova/starship.toml``.
    Without one the shell uses Nova's built-in minimal prompt.
    """
    env = os.environ.get("NOVA_STARSHIP_CONFIG")
    candidates = [Path(env)] if env else []
    candidates.append(Path.home() / ".config" / "nova" / "starship.toml")
    return next((c for c in candidates if c.is_file()), None)

DEFAULT_SETTINGS: dict[str, str] = {
    "model": "",
    "temperature": "0.7",
    # "auto": the largest context that keeps the selected model fully on the
    # GPU, measured with Ollama (nova.llm.context_fit, AD-028); a number is a
    # manual override.
    "num_ctx": "auto",
    "system_prompt": "",
    "ollama_url": "http://localhost:11434",
    "close_to_tray": "true",
    "auto_approve_writes": "false",
    "always_on_top": "false",
    "reduce_motion": "false",
    # Global show/hide key registered with KDE ("" = none), AD-035.
    "toggle_shortcut": "",
}

from nova.rpc.settings import SettingsMixin
from nova.rpc.chat import ChatMixin
from nova.rpc.conversations import ConversationsMixin
from nova.rpc.models import ModelsMixin
from nova.rpc.skills import SkillsMixin
from nova.rpc.mcp import McpMixin
from nova.rpc.memories import MemoriesMixin
from nova.rpc.terminal import TerminalMixin
from nova.rpc.system import SystemMixin
from nova.rpc.shortcut import ShortcutMixin


class NovaRpc(
    SettingsMixin,
    ChatMixin,
    ConversationsMixin,
    ModelsMixin,
    SkillsMixin,
    McpMixin,
    MemoriesMixin,
    TerminalMixin,
    SystemMixin,
    ShortcutMixin,
):
    """Bridges the agent stack to a JSON-RPC transport."""

    def __init__(self, transport: "Transport") -> None:
        self._transport = transport
        self._provider = None  # created lazily from settings
        self._registry = ToolRegistry()
        self._registry.register_linux_tools()
        self._registry.register_web_tools()
        self._registry.register_filesystem_tools()
        self._registry.register_service_tools()
        self._registry.register_ui_tools(self)
        self._shell_session: ShellSession | None = None
        self._term_queue: Any = None
        self._term_task: asyncio.Task | None = None
        self._store: MemoryStore | None = None
        self._skill_loader: SkillLoader | None = None
        self._agent: Agent | None = None
        self._settings = dict(DEFAULT_SETTINGS)
        self._conversation_id: int | None = None
        self._send_task: asyncio.Task | None = None
        self._last_user_prompt = ""
        self._pending_permission: asyncio.Future | None = None
        self._grant_tool: Any | None = None
        self._pending_choice: asyncio.Future | None = None
        self._choice_options: list[str] = []
        self._mcp_manager: Any | None = None
        self._fit_task: asyncio.Task | None = None

    async def setup(self) -> None:
        """Initialize memory, skills and agent (call once at startup)."""
        from nova.llm.ollama import OllamaProvider

        store_path = None
        for i, arg in enumerate(sys.argv):
            if arg == "--db" and i + 1 < len(sys.argv):
                store_path = sys.argv[i + 1]
        self._store = MemoryStore(store_path) if store_path else MemoryStore()
        self._restore_settings()

        self._skill_loader = None
        skills_dir = RESOURCES_DIR / "skills"
        if skills_dir.is_dir():
            # Bundled skills load from the resources dir; skills created from chat
            # live next to the database, outside the source tree.
            self._skill_loader = SkillLoader(
                skills_dir,
                registry=self._registry,
                store=self._store,
                user_dir=USER_SKILLS_DIR,
            )
            self._registry.register_skill_maker_tools(
                self._skill_loader, self._store
            )
            loaded = self._skill_loader.register_all(self._registry)
            if loaded:
                print(f"Nova: loaded {loaded} tools from skills", file=sys.stderr)
            self.emit("skill_list", json.loads(self._skill_loader.to_json()))

        # Shared persistent shell (cwd/env survive between agent turns and
        # power the future embedded terminal).
        self._shell_session = ShellSession(starship_config=_starship_config())
        self._registry.register_shell_tools(self._shell_session)

        self._provider = OllamaProvider(base_url=self._settings["ollama_url"])
        self._agent = Agent(provider=self._provider, registry=self._registry)
        self._agent.model = self._settings["model"]
        self._agent.options = {
            "temperature": float(self._settings["temperature"]),
            "num_ctx": self._resolved_num_ctx(),
        }
        self._agent.context.system_prompt = self._load_system_prompt()
        self._agent.tool_callback = self._on_tool_event
        self._registry.permissions = PermissionSystem(
            approval_callback=self._request_permission,
            auto_approve_writes=self._settings["auto_approve_writes"] == "true",
        )
        self._restore_grants()
        self._registry.register_memory_tools(self._store)
        self._apply_context()
        # MCP servers: launch enabled ones; tools register themselves as
        # each connection reaches the connected state.
        from nova.mcp.manager import McpManager

        self._mcp_manager = McpManager(
            self._registry,
            notify=lambda st: self.emit("mcp_status", json.dumps(st)),
            on_auth=lambda name, info: self.emit(
                "mcp_auth_required", json.dumps({**info, "server": name})
            ),
        )
        for warning in await self._mcp_manager.start_all():
            print(f"Nova mcp: {warning}", file=sys.stderr)
        await self._start_shortcut()
        # NOTE: no start_conversation() here — load_history() (Server.run)
        # picks the last real conversation, or creates one only when the
        # store is empty. Creating one per startup leaked empty conversations
        # and made every app start show an empty chat.

    def emit(self, event: str, *params: Any) -> None:
        self._transport.send({"event": event, "params": list(params)})
        # Frontend aliases: also emit events under the names the React
        # renderer expects (useNova hook).
        alias, data = _ALIASES.get(event, (None, None))
        if alias is not None:
            self._transport.send({"event": alias, "params": [data(*params) if callable(data) else data]})

    def quit(self) -> None:
        """Called by the shell when the app is quitting."""
        if self._send_task is not None and not self._send_task.done():
            self._send_task.cancel()
        if self._term_task is not None and not self._term_task.done():
            self._term_task.cancel()
        self._term_task = None
        self._term_queue = None
        if self._shell_session is not None:
            self._shell_session.close()
        self._stop_shortcut()

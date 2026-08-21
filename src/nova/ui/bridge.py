"""Python-QML bridge for Nova UI."""

import asyncio
import json
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication
from qasync import asyncSlot

from nova.agent.agent import Agent
from nova.agent.context import DEFAULT_SYSTEM_PROMPT
from nova.llm.models import Role
from nova.memory.store import MemoryStore

if TYPE_CHECKING:
    from nova.tools.tool import Tool

DEFAULT_SETTINGS: dict[str, str] = {
    "model": "",
    "temperature": "0.7",
    "num_ctx": "4096",
    "system_prompt": "",
    "ollama_url": "http://localhost:11434",
    "close_to_tray": "true",
    "auto_approve_writes": "false",
    "always_on_top": "false",
    "reduce_motion": "false",
}

NUM_CTX_OPTIONS = ["2048", "4096", "8192", "16384", "32768", "65536"]


class ChatBridge(QObject):
    """Bridges the Python Agent to the QML UI."""

    messageAdded = Signal(str, str, bool)  # role, content, is_streaming
    streamUpdated = Signal(str)  # token
    streamCompleted = Signal()
    statusChanged = Signal(str)  # status message
    permissionRequested = Signal(str, str, str, str)  # tool, risk, permission, args
    modelsListed = Signal(list)  # available model names
    streamingChanged = Signal(bool)  # generation in progress
    metricsUpdated = Signal(str)  # system metrics JSON (gpu/memory/cpu)
    responseMetrics = Signal(str)  # last response stats JSON
    modelInfoChanged = Signal()  # selected model info may have changed
    toolStarted = Signal(str, str)  # tool name, args JSON
    toolFinished = Signal(str, bool, int, str)  # name, ok, duration_ms, error

    def __init__(
        self,
        agent: Agent,
        store: MemoryStore | None = None,
        quit_cb=None,
        skill_loader=None,
    ) -> None:
        super().__init__()
        self.agent = agent
        self.store = store
        self.skill_loader = skill_loader
        self._quit_cb = quit_cb or QApplication.quit
        self._allow_close = False
        self._conversation_id: int | None = None
        self._pending_permission: asyncio.Future | None = None
        self._grant_tool: "Tool" | None = None
        self._send_task: asyncio.Task | None = None
        self._last_user_prompt = ""
        self._settings = dict(DEFAULT_SETTINGS)
        self._model_infos: dict[str, Any] = {}
        agent.tool_callback = self._on_tool_event

    # ---- Tool activity events ------------------------------------------

    async def _on_tool_event(self, event: dict[str, Any]) -> None:
        """Forward agent tool events to the UI."""
        kind = event.get("type")
        if kind == "start":
            self.toolStarted.emit(
                event["name"], json.dumps(event.get("args", {}), default=str)
            )
        elif kind == "end":
            self.toolFinished.emit(
                event["name"],
                bool(event.get("ok")),
                int(event.get("duration_ms", 0)),
                str(event.get("error", "")),
            )

    # ---- Permission approval ------------------------------------------

    async def request_permission(self, tool: "Tool", args: dict[str, Any]) -> bool:
        """Approval callback for PermissionSystem — asks the QML UI."""
        if self._pending_permission is not None:
            return False
        self._pending_permission = asyncio.get_running_loop().create_future()
        self._grant_tool = tool
        args_json = json.dumps(args, default=str)
        self.permissionRequested.emit(
            tool.name, tool.risk.value, tool.permission, args_json
        )
        return await self._pending_permission

    @Slot(bool, bool)
    def respondPermission(self, approved: bool, remember: bool = False) -> None:
        """QML slot — resolves the pending permission request.

        With remember=True the permission is granted persistently.
        """
        if self._pending_permission is None:
            return
        future, self._pending_permission = self._pending_permission, None
        tool = self._grant_tool
        self._grant_tool = None
        if approved and remember and tool is not None:
            if self.store is not None:
                self.store.set_setting(f"perm_grant_{tool.permission}", "true")
            permissions = getattr(self.agent.registry, "permissions", None)
            if permissions is not None:
                permissions.grant(tool.permission)
        future.set_result(approved)

    # ---- Memory --------------------------------------------------------

    def start_conversation(self) -> None:
        """Create a fresh conversation in memory (explicit save)."""
        if self.store is None:
            self._conversation_id = None
            return
        self._conversation_id = self.store.create_conversation()

    def load_history(self) -> None:
        """Seed the UI and agent context from the last stored conversation."""
        if self.store is None:
            return
        conversations = self.store.list_conversations()
        if not conversations:
            self.start_conversation()
            return
        self._conversation_id = conversations[0]["id"]
        for message in self.store.get_messages(self._conversation_id):
            if message.role == Role.USER:
                self._last_user_prompt = message.content
                self.agent.context.add_user(message.content)
                self.messageAdded.emit("user", message.content, False)
            elif message.role == Role.ASSISTANT:
                self.agent.context.add_assistant(message.content)
                self.messageAdded.emit("assistant", message.content, False)

    def _save_message(self, role: Role, content: str) -> None:
        if self.store is None or self._conversation_id is None or not content:
            return
        self.store.add_message(self._conversation_id, role, content)

    # ---- Settings --------------------------------------------------------

    def restore_settings(self) -> None:
        """Apply persisted settings to the agent and permission system."""
        if self.store is not None:
            for key in self._settings:
                stored = self.store.get_setting(key)
                if stored is not None:
                    self._settings[key] = stored
            for key, value in self.store.list_settings():
                if key.startswith("perm_grant_") and value == "true":
                    permissions = getattr(self.agent.registry, "permissions", None)
                    if permissions is not None:
                        permissions.grant(key.removeprefix("perm_grant_"))
        self._apply_settings()

    def _apply_settings(self) -> None:
        s = self._settings
        if s["model"]:
            self.agent.model = s["model"]
        self.agent.options = {
            "temperature": _to_float(s["temperature"], 0.7),
            "num_ctx": int(s["num_ctx"]),
        }
        self.agent.context.system_prompt = (
            s["system_prompt"] or DEFAULT_SYSTEM_PROMPT
        )
        if getattr(self.agent.registry, "permissions", None) is not None:
            self.agent.registry.permissions.auto_approve_writes = (
                s["auto_approve_writes"] == "true"
            )

    @Slot(result=str)
    def getSettings(self) -> str:
        """Return all settings as JSON for the settings dialog."""
        return json.dumps(self._settings)

    @Slot(str)
    def saveSettings(self, settings_json: str) -> None:
        """Persist settings from the dialog and apply them."""
        try:
            incoming = json.loads(settings_json)
        except json.JSONDecodeError:
            self.statusChanged.emit("Error: invalid settings data")
            return
        for key, value in incoming.items():
            if key not in self._settings:
                continue
            text = str(value)
            self._settings[key] = text
            if self.store is not None:
                self.store.set_setting(key, text)
        self._apply_settings()
        self.statusChanged.emit("Settings saved")

    @Slot(str)
    def setOllamaUrl(self, url: str) -> None:
        """Update the Ollama base URL (applied on next start)."""
        self._settings["ollama_url"] = url
        if self.store is not None:
            self.store.set_setting("ollama_url", url)

    @Slot(result=str)
    def ollamaUrl(self) -> str:
        return self._settings["ollama_url"]

    # ---- Model selection -------------------------------------------------

    @asyncSlot()
    async def listModels(self) -> None:
        """Fetch available Ollama models and emit them to the UI."""
        try:
            infos = await self.agent.provider.list_models()
            self._model_infos = {info.name: info for info in infos}
            self.modelsListed.emit([info.name for info in infos])
            self.modelInfoChanged.emit()
        except Exception as e:
            self.statusChanged.emit(f"Error listing models: {e}")

    @Slot(str)
    def selectModel(self, name: str) -> None:
        """Select a model for the agent and persist the choice."""
        self.agent.model = name
        self._settings["model"] = name
        if self.store is not None:
            self.store.set_setting("model", name)
        self.modelInfoChanged.emit()
        self.statusChanged.emit(f"Model: {name}")

    @Slot(result=str)
    def currentModel(self) -> str:
        return self.agent.model or "(default)"

    # ---- Skills ----------------------------------------------------------

    @Slot(result=str)
    def skills(self) -> str:
        """Return all discovered skills as JSON for the skills dialog."""
        if self.skill_loader is None:
            return "[]"
        return self.skill_loader.to_json()

    @Slot(str, bool)
    def toggleSkill(self, name: str, enabled: bool) -> None:
        """Enable or disable a skill (persisted; registry updated live)."""
        if self.skill_loader is None:
            return
        if self.skill_loader.set_enabled(name, enabled):
            state = "enabled" if enabled else "disabled"
            self.statusChanged.emit(f"Skill '{name}' {state}")
        else:
            self.statusChanged.emit(f"Skill '{name}' not found")

    # ---- Metrics / side panel -------------------------------------------

    @asyncSlot()
    async def refreshMetrics(self) -> None:
        """Query system metrics and emit them as JSON to the UI."""
        registry = getattr(self.agent, "registry", None)
        if registry is None:
            return
        out: dict[str, Any] = {}
        for name in ("linux.gpu_info", "linux.memory_info", "linux.cpu_info"):
            result = await registry.execute(name)
            key = name.split(".")[1].removesuffix("_info")
            out[key] = (
                result.data if result.success else {"error": result.error}
            )
        self.metricsUpdated.emit(json.dumps(out))

    @Slot(result=str)
    def modelInfo(self) -> str:
        """Return JSON describing the currently selected model."""
        info = self._model_infos.get(self.agent.model or "")
        if info is None:
            return "{}"
        return json.dumps(
            {
                "name": info.name,
                "parameter_size": info.parameter_size,
                "quantization": info.quantization,
                "context_length": info.context_length,
                "size_bytes": info.size,
            }
        )

    # ---- Chat ----------------------------------------------------------

    @asyncSlot(str)
    async def sendMessage(self, text: str) -> None:
        if not text.strip():
            return
        self._send_task = asyncio.current_task()
        self._last_user_prompt = text
        self._save_message(Role.USER, text)
        self.messageAdded.emit("user", text, False)
        self.messageAdded.emit("assistant", "", True)

        full_response = ""
        stats: dict[str, Any] = {
            "prompt_tokens": 0,
            "tokens": 0,
            "tools": 0,
            "model": self.agent.model or "",
        }
        self.streamingChanged.emit(True)
        try:
            async for chunk in self.agent.send(text):
                if chunk.token:
                    full_response += chunk.token
                    self.streamUpdated.emit(chunk.token)
                if chunk.prompt_eval_count:
                    stats["prompt_tokens"] = max(
                        stats["prompt_tokens"], chunk.prompt_eval_count
                    )
                if chunk.eval_count:
                    stats["tokens"] = max(stats["tokens"], chunk.eval_count)
                if chunk.tool_calls:
                    stats["tools"] += len(chunk.tool_calls)
                if chunk.prompt_eval_duration_ns:
                    stats["prompt_eval_ns"] = chunk.prompt_eval_duration_ns
                if chunk.eval_duration_ns:
                    stats["eval_ns"] = chunk.eval_duration_ns
                if chunk.total_duration_ns:
                    stats["total_ns"] = chunk.total_duration_ns
            self.streamCompleted.emit()
        except asyncio.CancelledError:
            self.statusChanged.emit("Generation stopped")
            self.streamCompleted.emit()
        except Exception as e:
            self.statusChanged.emit(f"Error: {str(e)}")
            self.streamCompleted.emit()
        finally:
            if self._send_task is asyncio.current_task():
                self._send_task = None
            self.streamingChanged.emit(False)
            if full_response:
                self._save_message(Role.ASSISTANT, full_response)
            if stats["tokens"] and full_response:
                self.responseMetrics.emit(json.dumps(stats))

    @Slot()
    def stopGeneration(self) -> None:
        """Cancel the in-flight generation task."""
        if self._send_task is not None and not self._send_task.done():
            self._send_task.cancel()

    @Slot()
    def regenerateLast(self) -> None:
        """Re-run the last user prompt (replaces the previous answer)."""
        if not self._last_user_prompt:
            return
        if self._send_task is not None and not self._send_task.done():
            return
        self.agent.context.pop_last_assistant()
        self.agent.context.pop_last_user()
        if self.store is not None and self._conversation_id is not None:
            self.store.delete_last_message(self._conversation_id)  # assistant
            self.store.delete_last_message(self._conversation_id)  # user
        self.messageAdded.emit("regenerate", "", False)
        self._send_task = asyncio.ensure_future(self.sendMessage(self._last_user_prompt))

    @Slot(str)
    def copyToClipboard(self, text: str) -> None:
        """Copy text to the system clipboard."""
        QGuiApplication.clipboard().setText(text)

    @Slot()
    def clearHistory(self) -> None:
        if self.store is not None and self._conversation_id is not None:
            self.store.delete_conversation(self._conversation_id)
            self._conversation_id = None
        self.start_conversation()
        self.agent.reset()
        self._last_user_prompt = ""

    # ---- Quit ----------------------------------------------------------

    @Slot()
    def quitApp(self) -> None:
        """Quit the application (tray Quit action)."""
        self._allow_close = True
        self._quit_cb()

    @Slot(result=bool)
    def allowClose(self) -> bool:
        """Whether window closing is allowed (vs. hiding to tray)."""
        if self._allow_close:
            return True
        return self._settings["close_to_tray"] != "true"


def _to_float(value: str, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
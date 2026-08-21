"""JSON-RPC backend for the Electron frontend.

Nova's agent stack (Agent, ToolRegistry, skills, memory, permissions) is
exposed to the Electron renderer over stdio using newline-delimited JSON.

Protocol (line-based, UTF-8):

    -> {"id": 1, "method": "sendMessage", "params": ["hello"]}
    <- {"id": 1, "result": null}
    -> {"id": 2, "method": "getSettings"}
    <- {"id": 2, "result": {"model": "", ...}}
    <- {"event": "messageAdded", "params": ["user", "hello", false]}

Methods mirror the former QML ChatBridge slots; events mirror its signals.
"""

import asyncio
import inspect
import json
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

from nova.agent.agent import Agent
from nova.memory.store import MemoryStore
from nova.security.permissions import PermissionSystem
from nova.skills.loader import SkillLoader
from nova.tools.registry import ToolRegistry

# Event name mapping: backend event → (frontend event name, data transform)
# The frontend useNova hook expects different event names than the old QML bridge.
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
    "metricsUpdated": ("system", lambda json_str: __import__("json").loads(json_str)),
}

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


class NovaRpc:
    """Bridges the agent stack to a JSON-RPC transport."""

    def __init__(self, transport: "Transport") -> None:
        self._transport = transport
        self._provider = None  # created lazily from settings
        self._registry = ToolRegistry()
        self._registry.register_linux_tools()
        self._registry.register_web_tools()
        self._store: MemoryStore | None = None
        self._skill_loader: SkillLoader | None = None
        self._agent: Agent | None = None
        self._settings = dict(DEFAULT_SETTINGS)
        self._conversation_id: int | None = None
        self._send_task: asyncio.Task | None = None
        self._last_user_prompt = ""
        self._pending_permission: asyncio.Future | None = None
        self._grant_tool: Any | None = None

    # ---- Lifecycle ------------------------------------------------------

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
        from pathlib import Path

        skills_dir = Path(__file__).resolve().parent.parent.parent / "skills"
        if skills_dir.is_dir():
            self._skill_loader = SkillLoader(
                skills_dir, registry=self._registry, store=self._store
            )
            loaded = self._skill_loader.register_all(self._registry)
            if loaded:
                print(f"Nova: loaded {loaded} tools from skills", file=sys.stderr)
            self.emit("skill_list", json.loads(self._skill_loader.to_json()))

        self._provider = OllamaProvider(base_url=self._settings["ollama_url"])
        self._agent = Agent(provider=self._provider, registry=self._registry)
        self._agent.model = self._settings["model"]
        self._agent.options = {
            "temperature": float(self._settings["temperature"]),
            "num_ctx": int(self._settings["num_ctx"]),
        }
        self._agent.context.system_prompt = self._load_system_prompt()
        self._agent.tool_callback = self._on_tool_event
        self._registry.permissions = PermissionSystem(
            approval_callback=self._request_permission,
            auto_approve_writes=self._settings["auto_approve_writes"] == "true",
        )
        self._restore_grants()
        # NOTE: no start_conversation() here — load_history() (Server.run)
        # picks the last real conversation, or creates one only when the
        # store is empty. Creating one per startup leaked empty conversations
        # and made every app start show an empty chat.

    def _restore_settings(self) -> None:
        if self._store is None:
            return
        for key in self._settings:
            stored = self._store.get_setting(key)
            if stored is not None:
                self._settings[key] = stored

    def _load_system_prompt(self) -> str:
        """Load system prompt from agent.md, falling back to settings/default."""
        from pathlib import Path

        candidates = [
            Path.home() / ".config" / "nova" / "agent.md",
            Path(__file__).resolve().parent.parent.parent / "agent.md",
        ]
        for path in candidates:
            if path.is_file():
                content = path.read_text().strip()
                if content:
                    return content
        return (
            self._settings.get("system_prompt", "")
            or "You are Nova, a helpful local AI desktop assistant."
        )

    def _restore_grants(self) -> None:
        if self._store is None:
            return
        for key, value in self._store.list_settings():
            if key.startswith("perm_grant_") and value == "true":
                self._registry.permissions.grant(key.removeprefix("perm_grant_"))

    def start_conversation(self) -> None:
        if self._store is None:
            self._conversation_id = None
            return
        self._conversation_id = self._store.create_conversation()

    def load_history(self) -> None:
        """Seed context from the last stored conversation."""
        if self._store is None or self._agent is None:
            return
        conversations = self._store.list_conversations()
        if not conversations:
            self.start_conversation()
            return
        from nova.llm.models import Role

        self._conversation_id = conversations[0]["id"]
        for message in self._store.get_messages(self._conversation_id):
            if message.role == Role.USER:
                self._last_user_prompt = message.content
                self._agent.context.add_user(message.content)
                self.emit("messageAdded", "user", message.content, False)
            elif message.role == Role.ASSISTANT:
                self._agent.context.add_assistant(message.content)
                self.emit("messageAdded", "assistant", message.content, False)

    # ---- Transport helpers ----------------------------------------------

    def emit(self, event: str, *params: Any) -> None:
        self._transport.send({"event": event, "params": list(params)})
        # Frontend aliases: also emit events under the names the React
        # renderer expects (useNova hook).
        alias, data = _ALIASES.get(event, (None, None))
        if alias is not None:
            self._transport.send({"event": alias, "params": [data(*params) if callable(data) else data]})

    async def _on_tool_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "start":
            self.emit("toolStarted", event["name"], json.dumps(event.get("args", {}), default=str))
        elif kind == "end":
            self.emit(
                "toolFinished",
                event["name"],
                bool(event.get("ok")),
                int(event.get("duration_ms", 0)),
                str(event.get("error", "")),
            )

    async def _request_permission(self, tool, args) -> bool:
        if self._pending_permission is not None:
            return False
        self._pending_permission = asyncio.get_running_loop().create_future()
        self._grant_tool = tool
        self.emit(
            "permissionRequested",
            tool.name,
            tool.risk.value,
            tool.permission,
            json.dumps(args, default=str),
        )
        return await self._pending_permission

    # ---- RPC methods ------------------------------------------------------

    async def sendMessage(self, text: str) -> None:
        if self._agent is None or not text.strip():
            return
        self._send_task = asyncio.current_task()
        self._last_user_prompt = text
        self._save_message("user", text)
        self.emit("messageAdded", "user", text, False)
        self.emit("messageAdded", "assistant", "", True)

        from nova.llm.models import Role

        full_response = ""
        stats: dict[str, Any] = {
            "prompt_tokens": 0,
            "tokens": 0,
            "tools": 0,
            "model": self._agent.model or "",
        }
        self.emit("streamingChanged", True)
        try:
            async for chunk in self._agent.send(text):
                if chunk.token:
                    full_response += chunk.token
                    self.emit("streamUpdated", chunk.token)
                if chunk.prompt_eval_count:
                    stats["prompt_tokens"] = max(stats["prompt_tokens"], chunk.prompt_eval_count)
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
            self.emit("streamCompleted")
        except asyncio.CancelledError:
            self.emit("statusChanged", "Generation stopped")
            self.emit("streamCompleted")
        except Exception as e:
            self.emit("statusChanged", f"Error: {e}")
            self.emit("streamCompleted")
        finally:
            if self._send_task is asyncio.current_task():
                self._send_task = None
            self.emit("streamingChanged", False)
            if full_response:
                self._save_message("assistant", full_response)
            if stats["tokens"] and full_response:
                self.emit("responseMetrics", json.dumps(stats))

    def _save_message(self, role: str, content: str) -> None:
        if self._store is None or self._conversation_id is None or not content:
            return
        from nova.llm.models import Role

        self._store.add_message(self._conversation_id, Role(role), content)
        if role == "user" and self._store.auto_title(self._conversation_id, content):
            self.emit("conversation_list", self._store.list_conversations())

    def stopGeneration(self) -> None:
        if self._send_task is not None and not self._send_task.done():
            self._send_task.cancel()

    def regenerateLast(self) -> None:
        if not self._last_user_prompt or self._agent is None:
            return
        if self._send_task is not None and not self._send_task.done():
            return
        self._agent.context.pop_last_assistant()
        self._agent.context.pop_last_user()
        if self._store is not None and self._conversation_id is not None:
            self._store.delete_last_message(self._conversation_id)
            self._store.delete_last_message(self._conversation_id)
        self.emit("regenerate")
        self._send_task = asyncio.ensure_future(self.sendMessage(self._last_user_prompt))

    def clearHistory(self) -> None:
        self.stopGeneration()
        # If the active conversation is already empty, just reset the agent
        # instead of creating another blank one.
        if (
            self._store is not None
            and self._conversation_id is not None
            and not self._store.get_messages(self._conversation_id)
        ):
            if self._agent is not None:
                self._agent.reset()
            self._last_user_prompt = ""
            # Still emit events so the frontend stays in sync.
            self.emit("conversationSwitched", self._conversation_id)
            if self._store is not None:
                self.emit("conversation_list", self._store.list_conversations())
            return
        self.start_conversation()
        if self._agent is not None:
            self._agent.reset()
        self._last_user_prompt = ""
        if self._store is not None:
            self.emit("conversation_list", self._store.list_conversations())

    def listModels(self) -> None:
        async def _run() -> None:
            try:
                infos = await self._provider.list_models()
                self._model_infos = {info.name: info for info in infos}
                self.emit("modelsListed", [info.name for info in infos])
                # Emit model_info for the currently selected model
                current = self._agent.model if self._agent else ""
                info = self._model_infos.get(current) if current else None
                if info is not None:
                    self.emit("model_info", {
                        "model": info.name,
                        "details": {
                            "family": "",
                            "parameter_size": info.parameter_size,
                            "quantization": info.quantization,
                            "families": [],
                        },
                        "context_length": info.context_length,
                    })
                # Signal that Ollama is reachable
                self.emit("statusChanged", "connected")
            except Exception as e:
                self.emit("statusChanged", f"Error listing models: {e}")

        asyncio.ensure_future(_run())

    def selectModel(self, name: str) -> None:
        if self._agent is None:
            return
        self._agent.model = name
        self._settings["model"] = name
        if self._store is not None:
            self._store.set_setting("model", name)
        self.emit("modelInfoChanged")
        self.emit("statusChanged", f"Model: {name}")
        # Emit model_info with raw Ollama data for the frontend
        info = getattr(self, "_model_infos", {}).get(name)
        if info is not None:
            self.emit("model_info", {
                "model": info.name,
                "details": {
                    "family": "",
                    "parameter_size": info.parameter_size,
                    "quantization": info.quantization,
                    "families": [],
                },
                "context_length": info.context_length,
            })

    def currentModel(self) -> str:
        return self._agent.model if self._agent and self._agent.model else "(default)"

    def modelInfo(self) -> str:
        if self._agent is None:
            return "{}"
        info = getattr(self, "_model_infos", {}).get(self._agent.model or "")
        if info is None:
            return "{}"
        return json.dumps({
            "name": info.name,
            "parameter_size": info.parameter_size,
            "quantization": info.quantization,
            "context_length": info.context_length,
            "size_bytes": info.size,
        })

    def getSettings(self) -> str:
        self.emit("settings", self._settings)
        return json.dumps(self._settings)

    def saveSettings(self, settings_json: str) -> None:
        try:
            incoming = json.loads(settings_json)
        except json.JSONDecodeError:
            self.emit("statusChanged", "Error: invalid settings data")
            return
        for key, value in incoming.items():
            if key not in self._settings:
                continue
            text = str(value)
            self._settings[key] = text
            if self._store is not None:
                self._store.set_setting(key, text)
        if self._agent is not None:
            self._agent.options = {
                "temperature": float(self._settings["temperature"]),
                "num_ctx": int(self._settings["num_ctx"]),
            }
        if self._registry.permissions is not None:
            self._registry.permissions.auto_approve_writes = (
                self._settings["auto_approve_writes"] == "true"
            )
        self.emit("statusChanged", "Settings saved")

    def skills(self) -> str:
        if self._skill_loader is None:
            self.emit("skill_list", [])
            return "[]"
        data = json.loads(self._skill_loader.to_json())
        self.emit("skill_list", data)
        return json.dumps(data)

    def toggleSkill(self, name: str, enabled: bool) -> None:
        if self._skill_loader is None:
            return
        if self._skill_loader.set_enabled(name, enabled):
            state = "enabled" if enabled else "disabled"
            self.emit("statusChanged", f"Skill '{name}' {state}")
        else:
            self.emit("statusChanged", f"Skill '{name}' not found")

    def respondPermission(self, approved: bool, remember: bool = False) -> None:
        if self._pending_permission is None:
            return
        future, self._pending_permission = self._pending_permission, None
        tool = self._grant_tool
        self._grant_tool = None
        if approved and remember and tool is not None:
            if self._store is not None:
                self._store.set_setting(f"perm_grant_{tool.permission}", "true")
            if self._registry.permissions is not None:
                self._registry.permissions.grant(tool.permission)
        future.set_result(approved)

    def refreshMetrics(self) -> None:
        async def _run() -> None:
            if self._agent is None:
                return
            out: dict[str, Any] = {}
            for name in ("linux.gpu_info", "linux.memory_info", "linux.cpu_info"):
                result = await self._agent.registry.execute(name)
                key = name.split(".")[1].removesuffix("_info")
                out[key] = result.data if result.success else {"error": result.error}
            out["ts"] = time.time()
            self.emit("metricsUpdated", json.dumps(out))

        asyncio.ensure_future(_run())

    def allowClose(self) -> bool:
        return self._settings["close_to_tray"] != "true"

    def quit(self) -> None:
        """Called by the shell when the app is quitting."""
        if self._send_task is not None and not self._send_task.done():
            self._send_task.cancel()

    # ---- Conversation management -------------------------------------------

    def listConversations(self) -> str:
        if self._store is None:
            return "[]"
        conversations = self._store.list_conversations()
        self.emit("conversation_list", conversations)
        return json.dumps(conversations)

    def switchConversation(self, conversation_id: int) -> None:
        if self._store is None:
            return
        if self._agent is None:
            return
        self.stopGeneration()
        # Emit the switch BEFORE replaying messages: the UI clears its view
        # on conversationSwitched, so replayed messageAdded events must
        # arrive after the clear or the chat is wiped.
        self._conversation_id = conversation_id
        self._agent.reset()
        self.emit("conversationSwitched", conversation_id)
        from nova.llm.models import Role

        for message in self._store.get_messages(conversation_id):
            if message.role == Role.USER:
                self._agent.context.add_user(message.content)
                self.emit("messageAdded", "user", message.content, False)
            elif message.role == Role.ASSISTANT:
                self._agent.context.add_assistant(message.content)
                self.emit("messageAdded", "assistant", message.content, False)
        conversations = self._store.list_conversations()
        self.emit("conversation_list", conversations)

    def deleteConversation(self, conversation_id: int) -> None:
        if self._store is None:
            return
        self.stopGeneration()
        self._store.delete_conversation(conversation_id)
        if self._conversation_id == conversation_id:
            self._conversation_id = None
            if self._agent is not None:
                self._agent.reset()
        self.emit("conversationDeleted", conversation_id)
        conversations = self._store.list_conversations()
        self.emit("conversation_list", conversations)

    def renameConversation(self, conversation_id: int, title: str) -> None:
        if self._store is None:
            return
        self._store.rename_conversation(conversation_id, title)

    def openPath(self, path: str) -> None:
        """Open a file or directory with the system default handler."""
        import subprocess
        try:
            subprocess.Popen(["xdg-open", path])
        except Exception:
            pass


class Transport:
    """Line-delimited JSON transport over a binary stream (stdio).

    Writes flush immediately: when stdout is a pipe (Electron sidecar) the
    block-buffered sys.stdout.buffer would otherwise swallow messages until
    the process exits.
    """

    def __init__(self, reader: asyncio.StreamReader, writer: Any) -> None:
        self._reader = reader
        self._writer = writer

    def send(self, message: dict[str, Any]) -> None:
        self._writer.write((json.dumps(message) + "\n").encode("utf-8"))
        self._writer.flush()

    async def read_line(self) -> str:
        line = await self._reader.readline()
        if not line:
            raise EOFError
        return line.decode("utf-8").strip()


# Frontend → backend method name mapping (React uses shorter names).
_METHOD_ALIASES: dict[str, str] = {
    "send": "sendMessage",
    "clear": "clearHistory",
    "abort": "stopGeneration",
    "settings": "getSettings",
    "settings_save": "saveSettings",
    "system": "refreshMetrics",
    "toggle_skill": "toggleSkill",
    "permission_respond": "respondPermission",
    "open": "openPath",
}


class Server:
    """Runs the RPC server, dispatching requests to NovaRpc."""

    def __init__(self, rpc: NovaRpc, transport: Transport) -> None:
        self._rpc = rpc
        self._transport = transport
        self._methods: dict[str, Callable[..., Awaitable[Any]] | Callable[..., Any]] = {}

    def register(self, name: str) -> None:
        def decorator(fn: Callable) -> Callable:
            self._methods[name] = fn
            return fn

        return decorator

    async def run(self) -> None:
        await self._rpc.setup()
        self._rpc.load_history()
        # Trigger async initial loads (these emit events internally)
        self._rpc.listModels()
        self._rpc.refreshMetrics()
        self._rpc.emit("skill_list", json.loads(self._rpc._skill_loader.to_json()) if self._rpc._skill_loader else [])
        try:
            while True:
                try:
                    line = await self._transport.read_line()
                except EOFError:
                    break
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "method" not in message:
                    continue
                # Dispatch each request in its own task: a long-running
                # method (sendMessage streams for minutes) must not block
                # the read loop, otherwise stopGeneration/respondPermission
                # can never be received while a generation is in flight.
                asyncio.ensure_future(self._dispatch(message))
        finally:
            self._rpc.quit()

    def emit_async(self, event: str, *params: Any) -> None:
        """Emit an event from an async context."""
        asyncio.ensure_future(self._emit_event_async(event, *params))

    async def _emit_event_async(self, event: str, *params: Any) -> None:
        self._rpc.emit(event, *params)

    async def _dispatch(self, message: dict[str, Any]) -> None:
        raw_name = message.get("method")
        method_name = _METHOD_ALIASES.get(raw_name, raw_name)
        method = self._methods.get(method_name)
        request_id = message.get("id")
        if method is None:
            if request_id is not None:
                self._transport.send(
                    {"id": request_id, "error": {"code": -32601, "message": f"Method not found: {raw_name}"}}
                )
            return
        params = message.get("params") or []
        # Frontend sends dict params — match them by parameter name so
        # multi-key objects (e.g. settings) land on the right arguments.
        if isinstance(params, dict):
            sig = inspect.signature(method)
            params = [params[k] for k in sig.parameters if k in params]
        try:
            result = method(*params)
            if hasattr(result, "__await__"):
                result = await result
        except Exception as e:
            if request_id is not None:
                self._transport.send({"id": request_id, "error": {"code": -32000, "message": str(e)}})
            return
        if request_id is not None:
            self._transport.send({"id": request_id, "result": result})


def build_server(rpc: NovaRpc, transport: Transport) -> Server:
    server = Server(rpc, transport)
    for name in (
        "sendMessage",
        "stopGeneration",
        "regenerateLast",
        "clearHistory",
        "listModels",
        "selectModel",
        "currentModel",
        "modelInfo",
        "getSettings",
        "saveSettings",
        "skills",
        "toggleSkill",
        "respondPermission",
        "refreshMetrics",
        "allowClose",
        "quit",
        "listConversations",
        "switchConversation",
        "deleteConversation",
        "renameConversation",
        "openPath",
    ):
        server.register(name)(getattr(rpc, name))
    return server


async def _main() -> None:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin.buffer)
    transport = Transport(reader, sys.stdout.buffer)
    rpc = NovaRpc(transport)
    server = build_server(rpc, transport)
    await server.run()


def main() -> int:
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
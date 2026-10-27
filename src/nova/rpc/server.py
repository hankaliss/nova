"""Stdio transport and request dispatch for the JSON-RPC sidecar."""

import asyncio
import inspect
import json
import os
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from nova.rpc.core import NovaRpc


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
    "choice_select": "selectChoice",
    "open": "openPath",
    "terminal_open": "terminalOpen",
    "terminal_close": "terminalClose",
    "terminal_input": "terminalInput",
    "terminal_resize": "terminalResize",
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
        "getSkill",
        "createSkill",
        "respondPermission",
        "selectChoice",
        "refreshMetrics",
        "allowClose",
        "quit",
        "mcpStatus",
        "mcpRetryAuth",
        "mcpToggle",
        "mcpRestart",
        "mcpSave",
        "mcpShutdown",
        "listConversations",
        "switchConversation",
        "deleteConversation",
        "renameConversation",
        "listMemories",
        "addMemory",
        "deleteMemory",
        "openPath",
        "terminalOpen",
        "terminalClose",
        "terminalInput",
        "terminalResize",
        "shortcutStatus",
        "shortcutRecording",
        "getSetupStatus",
        "pullModel",
        "cancelPull",
        "markOnboardingDone",
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
    # Banner on stderr: makes it verifiable from a terminal run which
    # sidecar build is actually serving the UI (stale-process trap).
    import time as _t

    print(
        f"nova sidecar ready pid={os.getpid()} ts={_t.strftime('%H:%M:%S')} "
        "mcp=stderr-tap+probe-gate",
        file=sys.stderr,
        flush=True,
    )
    try:
        await server.run()
    finally:
        # Kill MCP child processes so they never outlive the sidecar.
        await rpc.mcpShutdown()


def main() -> int:
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
    return 0

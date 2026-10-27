"""Tool registry for Nova.

All tool execution goes through the registry so permissions are
validated before any tool runs.
"""

import asyncio
from typing import Any

from nova.security.permissions import PermissionDenied, PermissionSystem
from nova.tools.tool import RiskLevel, Tool, ToolResult


class ToolRegistry:
    """Registers and executes tools with permission checks."""

    def __init__(self, permissions: PermissionSystem | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self.permissions = permissions or PermissionSystem()

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def register_many(self, tools: list[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    def unregister(self, name: str) -> bool:
        """Remove a registered tool. Returns True if it was present."""
        return self._tools.pop(name, None) is not None

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name]

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def to_schemas(self) -> list[dict[str, Any]]:
        """Return tool definitions in Ollama function-calling format."""
        schemas = []
        for tool in self._tools.values():
            if not tool.model_visible:
                continue
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters or {
                            "type": "object",
                            "properties": {},
                        },
                    },
                }
            )
        return schemas

    async def execute(self, name: str, args: dict[str, Any] | None = None) -> ToolResult:
        """Run a tool the model asked for: hidden tools don't exist for it."""
        try:
            tool = self.get(name)
        except KeyError as e:
            return ToolResult(success=False, error=str(e))
        if not tool.model_visible:
            return ToolResult(success=False, error=f"Unknown tool: {name}")
        return await self._run(tool, args or {})

    async def execute_for_skill(self, name: str, args: dict[str, Any] | None = None) -> ToolResult:
        """Run a tool on a skill's behalf: READ only, hidden tools included.

        A skill runs inside a tool call the user already allowed; letting it
        chain into WRITE tools would skip that tool's own approval.
        """
        try:
            tool = self.get(name)
        except KeyError as e:
            return ToolResult(success=False, error=str(e))
        if tool.risk != RiskLevel.READ:
            return ToolResult(success=False, error=f"{name} is not a READ tool; skills can't call it")
        return await self._run(tool, args or {})

    async def _run(self, tool: Tool, args: dict[str, Any]) -> ToolResult:
        try:
            await self.permissions.authorize(tool, args)
            result = tool.implementation(**args)
            if asyncio.iscoroutine(result):
                result = await result
            return ToolResult(success=True, data=result)
        except (PermissionDenied, KeyError, ValueError) as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def register_linux_tools(self) -> None:
        """Register all Linux system tools (read-only)."""
        from nova.tools import linux

        self.register_many(linux.get_tools())

    def register_web_tools(self) -> None:
        """Register all web tools (read-only)."""
        from nova.tools import web

        self.register_many(web.get_tools())

    def register_service_tools(self) -> None:
        """Register the systemd service tools (status READ, control WRITE)."""
        from nova.tools import services

        self.register_many(services.get_tools())

    def register_filesystem_tools(self) -> None:
        """Register the filesystem tools (read-only, home directory only)."""
        from nova.tools import filesystem

        self.register_many(filesystem.get_tools())

    def register_ui_tools(self, rpc) -> list[Tool]:
        """Register interactive UI tools bound to an RPC choices handler."""
        from nova.tools import ui

        tools = ui.get_tools(rpc.request_choices)
        self.register_many(tools)
        return tools

    def register_shell_tools(self, session) -> list[Tool]:
        """Register shell tools bound to a persistent ShellSession."""
        from nova.tools import shell

        tools = shell.build(session)
        self.register_many(tools)
        return tools

    def register_memory_tools(self, store) -> list[Tool]:
        """Register the memory tools bound to a MemoryStore."""
        from nova.tools import memory

        tools = memory.build(store)
        self.register_many(tools)
        return tools

    def register_skill_maker_tools(
        self, loader, store=None
    ) -> list[Tool]:
        """Register skill-maker tools bound to a SkillLoader."""
        from nova.tools.skill_maker import build

        tools = build(loader, store)
        self.register_many(tools)
        return tools
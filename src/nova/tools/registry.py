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
        args = args or {}
        try:
            tool = self.get(name)
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
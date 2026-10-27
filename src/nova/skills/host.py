"""What a skill can use from Nova while it runs (AD-042).

A skill's tools.py only gets plain Python; this module lets it call other
registered tools, such as an MCP server's, by their short name. Only READ
tools are reachable (``ToolRegistry.execute_for_skill``), including the
MCP tools marked ``skill_only`` that the model never sees.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nova.tools.registry import ToolRegistry

_registry: ToolRegistry | None = None


def attach(registry: ToolRegistry | None) -> None:
    """Called once by the app with the live registry (None detaches)."""
    global _registry
    _registry = registry


def find_mcp_tool(short_name: str) -> str | None:
    """Full name (``mcp.<server>.<tool>``) of the first connected MCP tool
    called ``short_name``, whichever server provides it; None if absent."""
    if _registry is None:
        return None
    suffix = "." + short_name
    for tool in _registry.list_tools():
        if tool.name.startswith("mcp.") and tool.name.endswith(suffix):
            return tool.name
    return None


async def call_tool(name: str, args: dict[str, Any] | None = None, timeout: float = 20) -> Any:
    """Run a READ tool and return its data; raises RuntimeError on failure.

    The timeout matters for MCP servers waiting on a browser login: a skill
    report must not hang for minutes on the remote half.
    """
    if _registry is None:
        raise RuntimeError("no tool registry attached")
    try:
        result = await asyncio.wait_for(_registry.execute_for_skill(name, args), timeout)
    except asyncio.TimeoutError:
        raise RuntimeError(f"{name} did not answer within {timeout:.0f} s") from None
    if not result.success:
        raise RuntimeError(result.error or f"{name} failed")
    return result.data

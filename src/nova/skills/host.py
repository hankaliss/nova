"""What a skill can use from Nova while it runs (AD-042).

A skill's tools.py only gets plain Python; this module lets it call other
registered tools, such as an MCP server's, by their short name. Only READ
tools are reachable (``ToolRegistry.execute_for_skill``), including the
MCP tools marked ``skill_only`` that the model never sees. It also hands
skills a few app services by name (``service``): the Ollama URL, the chat
model and its system prompt, and selecting a model (AD-041).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nova.tools.registry import ToolRegistry

_registry: ToolRegistry | None = None
_services: dict[str, Callable[..., Any]] = {}


def attach(registry: ToolRegistry | None) -> None:
    """Called once by the app with the live registry (None detaches)."""
    global _registry
    _registry = registry


def provide(name: str, fn: Callable[..., Any] | None) -> None:
    """Called by the app: register (or, with None, remove) a service."""
    if fn is None:
        _services.pop(name, None)
    else:
        _services[name] = fn


def service(name: str) -> Callable[..., Any]:
    """An app service: ``ollama_url()``, ``current_model()``,
    ``system_prompt()``, ``select_model(name)``. Raises RuntimeError when
    Nova didn't provide it (e.g. a skill run outside the app)."""
    fn = _services.get(name)
    if fn is None:
        raise RuntimeError(f"Nova service '{name}' is not available")
    return fn


def model_tool_schemas() -> list[dict[str, Any]]:
    """The tool definitions the chat model gets, exactly as sent."""
    return _registry.to_schemas() if _registry is not None else []


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

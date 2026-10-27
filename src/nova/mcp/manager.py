"""Manages all configured MCP servers and bridges them to the ToolRegistry.

Tools are registered under ``mcp.<server>.<tool>`` and look completely
normal to the LLM: they flow through the same schemas, permission
checks, tool cards and error handling as native tools.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from mcp.types import TextContent

from nova.mcp.auth import is_verification_url, parse_auth_challenge
from nova.mcp.client import McpClient
from nova.mcp.config import (
    default_config_path,
    load_config,
    upsert_server_entry,
    validate_server_entry,
    auto_configure_github,
)
from nova.tools.registry import ToolRegistry
from nova.tools.tool import RiskLevel, Tool

logger = logging.getLogger(__name__)

_AUTH_MESSAGE = (
    "MCP server '{name}' requires browser authorization — follow the "
    "login prompt in Nova, then retry your request."
)

# While a login is pending, real server calls are gated: every call
# restarts the server's OAuth flow and invalidates the authorization URL
# already opened in the browser. One call is let through per interval as a
# probe — if the user has completed the login meanwhile it simply succeeds,
# otherwise the refreshed challenge re-opens the browser with a fresh link.
# (Fallback only: normally the stderr tap releases the gate instantly.)
_AUTH_PROBE_INTERVAL = 15.0

# Curated risk map for known servers. Tool names map to risk levels;
# anything not listed falls back to _default_risk().
_MCP_RISK_OVERRIDES: dict[str, dict[str, RiskLevel]] = {
    # GitHub MCP server (toolset repos,users): read ops are READ so repo
    # listing works without a prompt; mutating ops stay WRITE; delete is
    # DESTRUCTIVE requiring explicit confirmation.
    "github": {
        "create_branch": RiskLevel.WRITE,
        "create_or_update_file": RiskLevel.WRITE,
        "create_repository": RiskLevel.WRITE,
        "delete_file": RiskLevel.DESTRUCTIVE,
        "fork_repository": RiskLevel.WRITE,
        "push_files": RiskLevel.WRITE,
    },
}


def _default_risk(tool_name: str) -> RiskLevel:
    """Fallback heuristic when no curated override exists."""
    lowered = tool_name.lower()
    if lowered.startswith("delete_") or lowered.startswith("remove_"):
        return RiskLevel.DESTRUCTIVE
    if lowered.startswith(("get_", "list_", "search_", "read_", "fetch_")):
        return RiskLevel.READ
    return RiskLevel.WRITE


def _first_line(text: str, limit: int = 140) -> str:
    line = text.strip().splitlines()[0].strip() if text.strip() else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _tool_risk(client_name: str, tool_name: str) -> RiskLevel:
    overrides = _MCP_RISK_OVERRIDES.get(client_name, {})
    return overrides.get(tool_name, _default_risk(tool_name))


class McpManager:
    """Owns every MCP client connection and its registry presence."""

    def __init__(
        self,
        registry: ToolRegistry,
        notify: Callable[[list[dict[str, Any]]], None] | None = None,
        on_auth: Callable[[str, dict[str, str]], None] | None = None,
    ) -> None:
        self._registry = registry
        self._notify = notify
        self._on_auth = on_auth
        self.clients: dict[str, McpClient] = {}
        self.path = default_config_path()

    # ---- Lifecycle ------------------------------------------------------

    async def start_all(self) -> list[str]:
        """Load config and launch enabled servers. Returns warnings.

        Connections complete asynchronously; tools appear in the registry
        as each server reaches the connected state.
        """
        config, warnings = load_config(self.path)
        for warning in warnings:
            logger.warning("mcp config: %s", warning)

        # Auto-configure GitHub (OAuth browser login) if no github server exists
        if "github" not in config.servers:
            github_config = auto_configure_github()
            if github_config is not None:
                upsert_server_entry("github", github_config, self.path)
                warnings.append("Auto-configured GitHub MCP server (OAuth browser login)")
                config, _ = load_config(self.path)

        for name, server in config.servers.items():
            client = McpClient(name, server, on_state_change=self._on_state)
            self.clients[name] = client
            if server.enabled:
                # Background connect — don't block sidecar startup.
                asyncio.create_task(client.start(), name=f"mcp-start-{name}")
        return warnings

    async def shutdown(self) -> None:
        await asyncio.gather(
            *(client.shutdown() for client in self.clients.values()),
            return_exceptions=True,
        )

    def status(self) -> list[dict[str, Any]]:
        statuses = []
        for client in sorted(self.clients.values(), key=lambda c: c.name):
            entry = client.status()
            # Tool list for the UI's "Show tools": the server's own names, a
            # one-line description, and the risk Nova will gate it with.
            entry["tools"] = [
                {
                    "name": td.name,
                    "description": _first_line(getattr(td, "description", "") or ""),
                    "risk": _tool_risk(client.name, td.name).value,
                }
                for td in client.tools
            ]
            statuses.append(entry)
        return statuses

    # ---- State transitions ---------------------------------------------

    def _on_state(self, client: McpClient) -> None:
        try:
            if client.state == "connected":
                self._register_tools(client)
            elif client.state in ("failed", "disabled", "starting"):
                self._unregister_tools(client)
        except Exception:
            logger.exception("mcp/%s state handling failed", client.name)
        self._push_status()

    def _push_status(self) -> None:
        if self._notify is not None:
            try:
                self._notify(self.status())
            except Exception:
                logger.exception("mcp notify failed")

    def _register_tools(self, client: McpClient) -> None:
        count = 0
        for td in client.tools:
            full_name = f"mcp.{client.name}.{td.name}"
            try:
                self._registry.get(full_name)
            except KeyError:
                existing = None
            else:
                existing = full_name
            if existing is not None:
                # Our own namespace may be refreshed (reconnect); a clash
                # with anything outside mcp.* would be a config bug.
                if not full_name.startswith("mcp."):
                    logger.warning(
                        "mcp/%s: tool '%s' clashes with a native tool — skipped",
                        client.name,
                        td.name,
                    )
                    continue
                self._registry.unregister(full_name)

            schema = getattr(td, "input_schema", None) or getattr(td, "inputSchema", None)
            schema = schema if isinstance(schema, dict) else {}
            parameters = schema or {"type": "object", "properties": {}}

            def make_impl(c: McpClient, tool_name: str):
                async def impl(**kwargs: Any) -> str:
                    # Track whether we already waited for auth in this call
                    # (probe gate). If so, a challenge after the probe means
                    # auth didn't help — raise immediately instead of waiting
                    # on auth_event again (which would hang forever).
                    auth_waited = False
                    if c.auth is not None:
                        if time.monotonic() - c.auth_probed_at < _AUTH_PROBE_INTERVAL:
                            raise RuntimeError(_AUTH_MESSAGE.format(name=c.name))
                        c.auth_probed_at = time.monotonic()
                        try:
                            await asyncio.wait_for(c.auth_event.wait(), timeout=300.0)
                        except asyncio.TimeoutError:
                            raise RuntimeError(
                                "Authorization timed out — please try again."
                            )
                        c.auth_event.clear()
                        auth_waited = True
                    result = await c.call_tool(tool_name, kwargs)
                    text = _content_to_text(result.content)
                    is_error = _result_is_error(result)
                    challenge = parse_auth_challenge(text)
                    if challenge is not None and (
                        is_error or is_verification_url(challenge.url)
                    ):
                        if auth_waited:
                            self._set_auth(
                                c,
                                {"url": challenge.url, "code": challenge.code},
                            )
                            raise RuntimeError(_AUTH_MESSAGE.format(name=c.name))
                        self._set_auth(
                            c,
                            {"url": challenge.url, "code": challenge.code},
                        )
                        try:
                            await asyncio.wait_for(c.auth_event.wait(), timeout=300.0)
                        except asyncio.TimeoutError:
                            raise RuntimeError(
                                "Authorization timed out — please try again."
                            )
                        c.auth_event.clear()
                        result = await c.call_tool(tool_name, kwargs)
                        text = _content_to_text(result.content)
                        is_error = _result_is_error(result)
                        retry_challenge = parse_auth_challenge(text)
                        if retry_challenge is not None and (
                            is_error or is_verification_url(retry_challenge.url)
                        ):
                            self._set_auth(
                                c,
                                {"url": retry_challenge.url, "code": retry_challenge.code},
                            )
                            raise RuntimeError(_AUTH_MESSAGE.format(name=c.name))
                        if is_error:
                            raise RuntimeError(text or f"MCP tool '{tool_name}' failed")
                        self._clear_auth(c)
                        return text
                    if is_error:
                        raise RuntimeError(text or f"MCP tool '{tool_name}' failed")
                    self._clear_auth(c)
                    return text

                return impl

            tool = Tool(
                name=full_name,
                description=td.description or f"MCP tool from server '{client.name}'",
                parameters=parameters,
                risk=_tool_risk(client.name, td.name),
                permission=full_name,
                implementation=make_impl(client, td.name),
            )
            self._registry.register(tool)
            count += 1
        logger.info("mcp/%s: registered %d tools", client.name, count)

    def _unregister_tools(self, client: McpClient) -> None:
        prefix = f"mcp.{client.name}."
        for name in [t.name for t in self._registry.list_tools() if t.name.startswith(prefix)]:
            self._registry.unregister(name)

    # ---- RPC operations --------------------------------------------------

    async def set_enabled(self, name: str, enabled: bool) -> bool:
        client = self.clients.get(name)
        if client is None:
            return False
        if enabled:
            await client.restart()
        else:
            await client.stop()
        upsert_server_entry(
            name,
            {**client.config.model_dump(mode="json", exclude_none=True), "enabled": enabled},
            self.path,
        )
        return True

    async def restart(self, name: str) -> bool:
        client = self.clients.get(name)
        if client is None:
            return False
        await client.restart()
        return True

    async def save_server(self, name: str, entry: dict[str, Any]) -> None:
        """Validate, persist and live-apply one server entry."""
        validate_server_entry(entry)  # raises on invalid input
        existing = self.clients.get(name)
        if existing is not None:
            await existing.shutdown()
            del self.clients[name]
        self._unregister_named(name)

        upsert_server_entry(name, entry, self.path)
        config, warnings = load_config(self.path)
        for warning in warnings:
            logger.warning("mcp config: %s", warning)
        fresh = config.servers.get(name)
        if fresh is None:
            return  # validation passed earlier but file round-trip failed? nothing to do
        client = McpClient(name, fresh, on_state_change=self._on_state)
        self.clients[name] = client
        if fresh.enabled:
            asyncio.create_task(client.start(), name=f"mcp-start-{name}")

    def _unregister_named(self, name: str) -> None:
        prefix = f"mcp.{name}."
        for tname in [t.name for t in self._registry.list_tools() if t.name.startswith(prefix)]:
            self._registry.unregister(tname)

    # ---- Auth challenges ---------------------------------------------------

    def _set_auth(self, client: McpClient, payload: dict[str, str]) -> None:
        """Record a pending login request; notify the UI on first sight or
        whenever the challenge actually changes (a changed URL means the old
        authorization link is dead and must be re-opened)."""
        unchanged = client.auth == payload
        client.auth = payload
        if not unchanged:
            client.auth_probed_at = time.monotonic()
            if self._on_auth is not None:
                try:
                    self._on_auth(client.name, payload)
                except Exception:
                    logger.exception("mcp/%s auth callback failed", client.name)
            self._push_status()

    def _clear_auth(self, client: McpClient) -> None:
        """Drop a satisfied login request once a call succeeds."""
        if client.auth is None:
            return
        client.auth = None
        self._push_status()

    def request_retry(self, name: str) -> bool:
        """The user reports completing the browser login: release the
        fail-fast gate so the next tool call can reach the server again."""
        client = self.clients.get(name)
        if client is None or client.auth is None:
            return False
        client.auth = None
        client.auth_event.set()
        self._push_status()
        return True


def _result_is_error(result: Any) -> bool:
    """mcp 1.x calls it isError, 2.x is_error."""
    return bool(getattr(result, "is_error", None) or getattr(result, "isError", False))


def _content_to_text(content: Any) -> str:
    """Flatten MCP content blocks into plain text for the model."""
    parts: list[str] = []
    for block in content or []:
        if isinstance(block, TextContent):
            parts.append(block.text)
        else:
            label = type(block).__name__
            parts.append(f"[unsupported {label} content omitted]")
    return "\n".join(parts)


__all__ = ["McpManager", "RiskLevel"]

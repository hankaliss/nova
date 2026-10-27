"""MCP servers: status, login retry, enable/restart/save, shutdown."""

import json


class McpMixin:
    """Part of :class:`nova.rpc.core.NovaRpc`; state lives on the instance."""

    def mcpStatus(self) -> str:
        if self._mcp_manager is None:
            return "[]"
        return json.dumps(self._mcp_manager.status())

    def mcpRetryAuth(self, name: str) -> bool:
        """User pressed 'signed in' in the auth dialog — unblock the server."""
        if self._mcp_manager is None:
            return False
        return self._mcp_manager.request_retry(name)

    async def mcpToggle(self, name: str, enabled: bool) -> bool:
        if self._mcp_manager is None:
            return False
        return await self._mcp_manager.set_enabled(name, enabled)

    async def mcpRestart(self, name: str) -> bool:
        if self._mcp_manager is None:
            return False
        return await self._mcp_manager.restart(name)

    async def mcpSave(self, name: str, entry: str) -> bool:
        """Validate + persist + live-apply one server entry (JSON string)."""
        if self._mcp_manager is None:
            raise RuntimeError("MCP manager unavailable")
        try:
            parsed = json.loads(entry)
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid JSON: {e}") from e
        await self._mcp_manager.save_server(name, parsed)
        return True

    async def mcpShutdown(self) -> None:
        if self._mcp_manager is not None:
            await self._mcp_manager.shutdown()

"""Permission system for Nova.

LLM-generated tool requests are untrusted. Every tool execution passes
through permission validation:

    LLM
     ↓
    Agent
     ↓
    Tool Registry
     ↓
    Permission Validation
     ↓
    Tool
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from nova.tools.tool import RiskLevel, Tool

ApprovalCallback = Callable[[Tool, dict[str, Any]], Awaitable[bool]]


class PermissionDenied(Exception):
    """Raised when a tool execution is not permitted."""


class PermissionSystem:
    """Validates tool requests against policy and user approval."""

    def __init__(
        self,
        approval_callback: ApprovalCallback | None = None,
        auto_approve_writes: bool = False,
    ) -> None:
        self.approval_callback = approval_callback
        self.auto_approve_writes = auto_approve_writes
        self._grants: set[str] = set()

    def grant(self, permission: str) -> None:
        """Grant a specific permission for this session (WRITE tools only;
        DESTRUCTIVE tools always ask)."""
        self._grants.add(permission)

    def revoke(self, permission: str) -> None:
        self._grants.discard(permission)

    def has_grant(self, permission: str) -> bool:
        return permission in self._grants

    def can_execute(self, tool: Tool) -> bool:
        """Policy check without user interaction."""
        if tool.risk == RiskLevel.READ:
            return True
        if tool.risk == RiskLevel.DESTRUCTIVE:
            return False
        if self.has_grant(tool.permission):
            return True
        return self.auto_approve_writes and tool.auto_approvable

    async def request_confirmation(self, tool: Tool, args: dict[str, Any]) -> bool:
        """Ask the user (via approval callback) whether to run this tool.

        Returns True when the tool may run. Without a callback the
        request is always denied.
        """
        if self.approval_callback is None:
            return False
        result = self.approval_callback(tool, args)
        if asyncio.iscoroutine(result):
            result = await result
        return bool(result)

    async def authorize(self, tool: Tool, args: dict[str, Any]) -> None:
        """Validate a tool request, raising PermissionDenied if not allowed.

        READ tools pass immediately. WRITE/DESTRUCTIVE tools ask for
        explicit user confirmation.
        """
        if self.can_execute(tool):
            return
        confirmed = await self.request_confirmation(tool, args)
        if not confirmed:
            if tool.risk == RiskLevel.DESTRUCTIVE:
                raise PermissionDenied(
                    f"Tool '{tool.name}' requires explicit user confirmation "
                    "(DESTRUCTIVE) — denied"
                )
            raise PermissionDenied(
                f"Tool '{tool.name}' requires WRITE permission — denied"
            )
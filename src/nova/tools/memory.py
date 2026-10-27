"""Memory tools — facts the user explicitly asks Nova to keep.

Saving goes through the permission dialog (WRITE, never covered by the
blanket auto-approve setting): text the model reads from web pages or files
could otherwise plant fake memories. Forgetting deletes, so it always asks.
"""

import time
from typing import Any

from nova.tools.tool import RiskLevel, Tool


def _row(memory: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": memory["id"],
        "fact": memory["content"],
        "saved": time.strftime("%Y-%m-%d", time.localtime(memory["created_at"])),
    }


def build(store) -> list[Tool]:
    """Build the memory tools bound to a ``MemoryStore``."""

    def remember(fact: str) -> str:
        memory_id = store.save_memory(fact)
        return f"Saved memory #{memory_id}."

    def search(query: str = "") -> list[dict[str, Any]]:
        if query.strip():
            return [_row(m) for m in store.search_memories(query, limit=20)]
        return [_row(m) for m in store.list_memories()[:20]]

    def forget(memory_id: int) -> str:
        if store.delete_memory(int(memory_id)):
            return f"Forgot memory #{memory_id}."
        raise ValueError(f"No memory #{memory_id}")

    return [
        Tool(
            name="memory.remember",
            description=(
                "Save a fact the user explicitly asked you to remember (one short "
                "sentence, in third person, e.g. 'The NAS is at 192.168.1.20'). "
                "Only when the user asks; never secrets, passwords or tokens. "
                "The user confirms each save."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "fact": {"type": "string", "description": "The fact to keep."},
                },
                "required": ["fact"],
            },
            risk=RiskLevel.WRITE,
            permission="memory.remember",
            auto_approvable=False,
            implementation=remember,
        ),
        Tool(
            name="memory.search",
            description=(
                "Search the facts the user asked you to remember. Relevant ones "
                "are already in your instructions; use this for anything else. "
                "An empty query lists the most recent."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Words to look for."},
                },
            },
            risk=RiskLevel.READ,
            permission="memory.read",
            implementation=search,
        ),
        Tool(
            name="memory.forget",
            description=(
                "Delete a remembered fact by its id (from memory.search). Only "
                "when the user asks; they confirm it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "memory_id": {"type": "integer", "description": "The memory's id."},
                },
                "required": ["memory_id"],
            },
            risk=RiskLevel.DESTRUCTIVE,
            permission="memory.forget",
            implementation=forget,
        ),
    ]

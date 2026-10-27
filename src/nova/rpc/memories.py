"""Memories: what the user asked Nova to keep, for the prompt and the panel."""

import time
from typing import Any

# Most memories added to one prompt, and their total size.
NOTE_MAX_ITEMS = 10
NOTE_MAX_CHARS = 2400


class MemoriesMixin:
    """Part of :class:`nova.rpc.core.NovaRpc`; state lives on the instance."""

    def _memory_note(self, text: str) -> str:
        """System-prompt block with the memories relevant to ``text``."""
        if self._store is None:
            return ""
        lines: list[str] = []
        used = 0
        for memory in self._store.search_memories(text, limit=NOTE_MAX_ITEMS):
            saved = time.strftime("%Y-%m-%d", time.localtime(memory["created_at"]))
            line = f"- {memory['content']} (saved {saved}, #{memory['id']})"
            if used + len(line) > NOTE_MAX_CHARS:
                break
            lines.append(line)
            used += len(line)
        if not lines:
            return ""
        return (
            "## Things the user asked you to remember\n"
            "Use them when they are relevant. They may be out of date; if one "
            "seems wrong, say so.\n" + "\n".join(lines)
        )

    def _memory_rows(self) -> list[dict[str, Any]]:
        return self._store.list_memories() if self._store is not None else []

    def _emit_memories(self) -> None:
        self.emit("memory_list", self._memory_rows())

    def listMemories(self) -> list[dict[str, Any]]:
        rows = self._memory_rows()
        self.emit("memory_list", rows)
        return rows

    def addMemory(self, content: str) -> int:
        """Typed by the user in the Memories panel (their own action, no prompt)."""
        if self._store is None:
            raise RuntimeError("Memory store unavailable")
        memory_id = self._store.save_memory(content)
        self._emit_memories()
        return memory_id

    def deleteMemory(self, memory_id: int) -> bool:
        if self._store is None:
            return False
        removed = self._store.delete_memory(int(memory_id))
        self._emit_memories()
        return removed

"""SQLite-backed persistent memory for Nova.

Separates four concerns:
* conversation history
* persistent user memories
* application configuration
"""

import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from nova.llm.models import Message, Role

DEFAULT_DB_PATH = Path(os.environ.get(
    "XDG_DATA_HOME", str(Path.home() / ".local" / "share")
)) / "nova" / "nova.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _now() -> int:
    return int(time.time())


class MemoryStore:
    """Persistent storage for conversations, memories and settings."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            # Migration: add title column if missing (pre-existing DBs)
            try:
                conn.execute("SELECT title FROM conversations LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE conversations ADD COLUMN title TEXT NOT NULL DEFAULT ''")

    # ---- Conversations -------------------------------------------------

    def create_conversation(self) -> int:
        now = _now()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO conversations (created_at, updated_at) VALUES (?, ?)",
                (now, now),
            )
            return cur.lastrowid

    def list_conversations(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_messages(self, conversation_id: int) -> list[Message]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages "
                "WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        return [
            Message(role=Role(row["role"]), content=row["content"]) for row in rows
        ]

    def add_message(self, conversation_id: int, role: Role, content: str) -> None:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (conversation_id, role.value, content, now),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )

    def delete_last_message(self, conversation_id: int) -> bool:
        """Delete the most recent message of a conversation. Returns True if one was removed."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
            if row is None:
                return False
            conn.execute("DELETE FROM messages WHERE id = ?", (row["id"],))
        return True

    def delete_conversation(self, conversation_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))

    def rename_conversation(self, conversation_id: int, title: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE conversations SET title = ? WHERE id = ?",
                (title, conversation_id),
            )

    def auto_title(self, conversation_id: int, content: str, limit: int = 48) -> bool:
        """Set a title from the first user message if the conversation has none.

        Returns True when a title was set (and the UI should refresh).
        """
        import re

        with self._connect() as conn:
            row = conn.execute(
                "SELECT title FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if row is None or row["title"]:
                return False
            text = content.strip().splitlines()[0] if content.strip() else ""
            text = re.sub(r"[#>*_`~]", "", text)
            text = re.sub(r"\s+", " ", text).strip()
            if not text:
                return False
            title = text if len(text) <= limit else text[:limit].rstrip() + "…"
            conn.execute(
                "UPDATE conversations SET title = ? WHERE id = ?",
                (title, conversation_id),
            )
        return True

    # ---- Memories ------------------------------------------------------

    def save_memory(self, content: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO memories (content, created_at) VALUES (?, ?)",
                (content, _now()),
            )
            return cur.lastrowid

    def list_memories(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, content, created_at FROM memories ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_memory(self, memory_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))

    # ---- Settings ------------------------------------------------------

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def list_settings(self) -> list[tuple[str, str]]:
        """Return all persisted settings as (key, value) pairs."""
        with self._connect() as conn:
            return list(conn.execute("SELECT key, value FROM settings"))

    # ---- Misc ----------------------------------------------------------

    def close(self) -> None:
        """No persistent connections; exists for interface symmetry."""
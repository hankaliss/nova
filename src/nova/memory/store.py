"""SQLite-backed persistent memory for Nova.

Separates four concerns:
* conversation history
* persistent user memories
* application configuration
"""

import os
import re
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

-- Full-text index over memories (external content, kept in sync by triggers).
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, content='memories', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _now() -> int:
    return int(time.time())


# Longest memory kept (characters); memories are short facts, not documents.
MEMORY_MAX_CHARS = 500

# Words too common to say whether a memory is relevant.
_STOPWORDS = frozenset(
    "the and for are but not you your yours with this that what when where which "
    "who how why can could would should will was were has have had does did from "
    "into about there their them they then than its it's i'm me my mine our ours "
    "please tell show give know remember is am be an a of to in on at or as".split()
)


def _match_query(text: str) -> str | None:
    """Turn free text into a safe FTS5 query: quoted prefix terms OR'ed.

    Only word characters reach FTS5, so punctuation or FTS syntax in a
    message can never break or steer the search.
    """
    words = []
    for word in re.findall(r"\w+", text.lower()):
        if len(word) < 2 or word in _STOPWORDS or word in words:
            continue
        words.append(word)
    if not words:
        return None
    return " OR ".join(f'"{w}"*' for w in words[:24])


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
            # Index memories saved before the full-text index existed (cheap:
            # memories are a few hundred short facts at most).
            conn.execute("INSERT INTO memories_fts(memories_fts) VALUES ('rebuild')")

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
                "SELECT role, content, created_at FROM messages "
                "WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        return [
            Message(
                role=Role(row["role"]),
                content=row["content"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def add_message(self, conversation_id: int, role: Role, content: str) -> int:
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
        return now

    def delete_last_turn(self, conversation_id: int) -> int:
        """Delete the last user message and everything after it. Returns the
        number of rows removed."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM messages WHERE conversation_id = ? AND role = 'user' "
                "ORDER BY id DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
            if row is None:
                return 0
            cur = conn.execute(
                "DELETE FROM messages WHERE conversation_id = ? AND id >= ?",
                (conversation_id, row["id"]),
            )
            return cur.rowcount

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
        """Store a memory (trimmed, capped); an identical one is reused."""
        content = " ".join(content.split())[:MEMORY_MAX_CHARS]
        if not content:
            raise ValueError("memory is empty")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM memories WHERE content = ?", (content,)
            ).fetchone()
            if row is not None:
                return row["id"]
            cur = conn.execute(
                "INSERT INTO memories (content, created_at) VALUES (?, ?)",
                (content, _now()),
            )
            return cur.lastrowid

    def list_memories(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, content, created_at FROM memories ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def search_memories(self, text: str, limit: int = 10) -> list[dict[str, Any]]:
        """Memories relevant to ``text``, best match first (FTS5 bm25)."""
        query = _match_query(text)
        if query is None:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT m.id, m.content, m.created_at FROM memories_fts f "
                "JOIN memories m ON m.id = f.rowid "
                "WHERE memories_fts MATCH ? ORDER BY bm25(memories_fts) LIMIT ?",
                (query, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_memory(self, memory_id: int) -> bool:
        """Delete one memory; returns whether it existed."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            return cur.rowcount > 0

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

    def delete_setting(self, key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))

    def list_settings(self) -> list[tuple[str, str]]:
        """Return all persisted settings as (key, value) pairs."""
        with self._connect() as conn:
            return list(conn.execute("SELECT key, value FROM settings"))

    # ---- Misc ----------------------------------------------------------

    def close(self) -> None:
        """No persistent connections; exists for interface symmetry."""
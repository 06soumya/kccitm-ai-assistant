"""
Persistent chat session management for KCCITM AI Assistant.

Sessions store full message history with pipeline metadata, enabling:
- Multi-turn conversations with context
- Feedback collection (Phase 8)
- Adaptive learning (Phase 9)

Usage:
    sm = SessionManager()
    session = await sm.create_session("user_1", "CSE Analysis")
    await sm.add_message(session.id, "user", "top 5 CSE students")
    await sm.add_message(session.id, "assistant", "...", {"route_used": "SQL"})
    history = await sm.get_chat_history(session.id)
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from config import settings
from db.sqlite_client import execute, fetch_all, fetch_one

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """A single message in a session."""
    id: str
    session_id: str
    role: str                                        # "user", "assistant", "system"
    content: str
    metadata: dict = field(default_factory=dict)    # route_used, sql_query, chunks_used, etc.
    created_at: str = ""


@dataclass
class Session:
    """A chat session with message history."""
    id: str
    user_id: str
    title: str = ""
    messages: list[Message] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class SessionManager:
    """
    Manages persistent chat sessions stored in SQLite (sessions.db).

    Each user can have multiple sessions.  Sessions store full message history
    with metadata about which pipeline was used, what SQL was executed, which
    chunks were retrieved, etc.  This metadata feeds the adaptive layer (Phase 8-9).
    """

    def __init__(self, db_path: str = None):
        self.db_path = db_path or settings.SESSION_DB

    # ── Session CRUD ─────────────────────────────────────────────────────────

    async def create_session(self, user_id: str, title: str = "") -> Session:
        """
        Create a new chat session.

        Args:
            user_id: The user who owns this session
            title: Optional title (auto-generated from first query if empty)

        Returns:
            New Session object
        """
        session_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        await execute(
            self.db_path,
            "INSERT INTO sessions (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, user_id, title, now, now),
        )

        return Session(id=session_id, user_id=user_id, title=title,
                       created_at=now, updated_at=now)

    async def get_session(self, session_id: str) -> Session | None:
        """Get a session with all its messages."""
        row = await fetch_one(
            self.db_path,
            "SELECT * FROM sessions WHERE id = ?",
            (session_id,),
        )
        if not row:
            return None

        messages_rows = await fetch_all(
            self.db_path,
            "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        )

        messages = [self._row_to_message(r) for r in messages_rows]

        return Session(
            id=row["id"],
            user_id=row["user_id"],
            title=row.get("title", ""),
            messages=messages,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def list_sessions(self, user_id: str, limit: int = 50) -> list[dict]:
        """
        List sessions for a user (most recent first).
        Returns lightweight dicts (no full message history).
        """
        rows = await fetch_all(
            self.db_path,
            """SELECT id, title, created_at, updated_at,
                      (SELECT COUNT(*) FROM messages WHERE session_id = sessions.id) AS message_count
               FROM sessions
               WHERE user_id = ?
               ORDER BY updated_at DESC
               LIMIT ?""",
            (user_id, limit),
        )
        return [dict(r) for r in rows]

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session and all its messages (CASCADE)."""
        await execute(
            self.db_path,
            "DELETE FROM sessions WHERE id = ?",
            (session_id,),
        )
        return True

    # ── Message operations ────────────────────────────────────────────────────

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict = None,
    ) -> Message:
        """
        Add a message to a session.

        Args:
            session_id: Session to add to
            role: "user" or "assistant"
            content: Message text
            metadata: Optional dict with pipeline info:
                      {route_used, sql_query, sql_rows, chunks_used,
                       cache_hit, response_time_ms, token_usage, ...}

        Returns:
            The created Message
        """
        msg_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        meta_json = json.dumps(metadata) if metadata else None

        await execute(
            self.db_path,
            "INSERT INTO messages (id, session_id, role, content, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (msg_id, session_id, role, content, meta_json, now),
        )

        # Update session's updated_at timestamp
        await execute(
            self.db_path,
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (now, session_id),
        )

        # Auto-generate title from first user message when title is empty
        if role == "user":
            session_row = await fetch_one(
                self.db_path,
                "SELECT title FROM sessions WHERE id = ?",
                (session_id,),
            )
            if session_row and not session_row.get("title"):
                title = content[:80] + ("..." if len(content) > 80 else "")
                await execute(
                    self.db_path,
                    "UPDATE sessions SET title = ? WHERE id = ?",
                    (title, session_id),
                )

        return Message(
            id=msg_id,
            session_id=session_id,
            role=role,
            content=content,
            metadata=metadata or {},
            created_at=now,
        )

    async def get_chat_history(self, session_id: str, limit: int = 20) -> list[dict]:
        """
        Get recent chat history as a list of {role, content} dicts.

        This is the format expected by the LLM client and orchestrator.

        Args:
            session_id: Session to get history for
            limit: Max messages to return (most recent)

        Returns:
            List of {"role": "user"|"assistant", "content": "..."} in chronological order
        """
        rows = await fetch_all(
            self.db_path,
            """SELECT role, content FROM messages
               WHERE session_id = ? AND role IN ('user', 'assistant')
               ORDER BY created_at DESC
               LIMIT ?""",
            (session_id, limit),
        )
        # Reverse to chronological order
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    async def get_message_with_metadata(self, message_id: str) -> Message | None:
        """Get a specific message with its full metadata. Used by feedback system (Phase 8)."""
        row = await fetch_one(
            self.db_path,
            "SELECT * FROM messages WHERE id = ?",
            (message_id,),
        )
        if not row:
            return None
        return self._row_to_message(row)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _row_to_message(self, row: dict) -> Message:
        """Convert a database row to a Message dataclass."""
        metadata = {}
        if row.get("metadata"):
            try:
                metadata = json.loads(row["metadata"])
            except json.JSONDecodeError:
                metadata = {}
        return Message(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            metadata=metadata,
            created_at=row.get("created_at", ""),
        )

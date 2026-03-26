"""
SQLite helper for KCCITM AI Assistant.

Creates and manages all SQLite databases for the project:
  - sessions.db  — chat sessions, messages, users
  - cache.db     — query response cache
  - feedback.db  — user feedback + implicit signals + training candidates
  - prompts.db   — prompt templates + FAQ entries

Usage:
    # CLI init (creates all .db files and tables):
    from db.sqlite_client import init_all_dbs
    from config import settings
    init_all_dbs(settings)

    # Async queries:
    from db.sqlite_client import fetch_all
    rows = await fetch_all(settings.SESSION_DB, "SELECT * FROM sessions")
"""

import asyncio
import logging
import os
import sqlite3
from typing import Any

import aiosqlite

from config import settings as _settings

logger = logging.getLogger(__name__)

# ── SQL DDL ──────────────────────────────────────────────────────────────────

_SESSIONS_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT CHECK(role IN ('user','assistant','system')),
    content TEXT NOT NULL,
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'faculty' CHECK(role IN ('admin','faculty')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS query_cache (
    id TEXT PRIMARY KEY,
    query_text TEXT NOT NULL,
    query_hash TEXT NOT NULL,
    query_embedding BLOB,
    response TEXT NOT NULL,
    route_used TEXT,
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    hit_count INTEGER DEFAULT 0,
    last_hit_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cache_hash ON query_cache(query_hash);
"""

_FEEDBACK_DDL = """
CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY,
    message_id TEXT,
    session_id TEXT,
    query_text TEXT NOT NULL,
    response_text TEXT NOT NULL,
    rating INTEGER,
    feedback_text TEXT,
    implicit_signals TEXT,
    route_used TEXT,
    sql_generated TEXT,
    chunks_used TEXT,
    reranker_scores TEXT,
    confidence_score REAL,
    quality_score REAL,
    healed INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS implicit_signals (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    signal_type TEXT,
    original_query TEXT,
    follow_up_query TEXT,
    time_gap_seconds INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chunk_analytics (
    chunk_id TEXT PRIMARY KEY,
    times_retrieved INTEGER DEFAULT 0,
    times_reranked_top5 INTEGER DEFAULT 0,
    times_in_final_context INTEGER DEFAULT 0,
    avg_reranker_score REAL DEFAULT 0.0,
    avg_query_quality_score REAL DEFAULT 0.0,
    last_retrieved_at TIMESTAMP,
    never_retrieved INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS training_candidates (
    id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    response TEXT NOT NULL,
    quality_score REAL,
    category TEXT,
    source TEXT,
    included_in_training INTEGER DEFAULT 0,
    training_run_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_PROMPTS_DDL = """
CREATE TABLE IF NOT EXISTS prompt_templates (
    id TEXT PRIMARY KEY,
    prompt_name TEXT NOT NULL,
    section_name TEXT NOT NULL,
    content TEXT NOT NULL,
    version INTEGER DEFAULT 1,
    is_active INTEGER DEFAULT 1,
    performance_score REAL,
    query_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS prompt_evolution_log (
    id TEXT PRIMARY KEY,
    prompt_name TEXT,
    section_name TEXT,
    old_version INTEGER,
    new_version INTEGER,
    change_reason TEXT,
    change_diff TEXT,
    approved_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS faq_entries (
    id TEXT PRIMARY KEY,
    canonical_question TEXT,
    answer TEXT NOT NULL,
    source_queries TEXT,
    avg_quality_score REAL,
    hit_count INTEGER DEFAULT 0,
    last_hit_at TIMESTAMP,
    status TEXT DEFAULT 'active',
    admin_verified INTEGER DEFAULT 0,
    data_version TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# Map: db_path_attr → DDL string
_DB_DDL_MAP = {
    "SESSION_DB": _SESSIONS_DDL,
    "CACHE_DB": _CACHE_DDL,
    "FEEDBACK_DB": _FEEDBACK_DDL,
    "PROMPTS_DB": _PROMPTS_DDL,
}


# ── Sync init (CLI) ───────────────────────────────────────────────────────────

def init_all_dbs(cfg=None) -> None:
    """
    Create all SQLite database files and initialize all tables.

    Safe to run multiple times (uses CREATE TABLE IF NOT EXISTS).
    Called from CLI: python -c "from db.sqlite_client import init_all_dbs; from config import settings; init_all_dbs(settings)"
    """
    if cfg is None:
        cfg = _settings

    GREEN = "\033[92m"
    RESET = "\033[0m"

    for attr, ddl in _DB_DDL_MAP.items():
        db_path = getattr(cfg, attr)
        # Ensure parent directory exists
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(ddl)
            conn.commit()
            print(f"{GREEN}✓ {attr}: {db_path} initialized{RESET}")
        finally:
            conn.close()


# ── Async helpers ─────────────────────────────────────────────────────────────

async def execute(db_path: str, sql: str, params: tuple = ()) -> None:
    """Execute a write statement (INSERT/UPDATE/DELETE)."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(sql, params)
        await db.commit()


async def fetch_one(db_path: str, sql: str, params: tuple = ()) -> dict | None:
    """Execute a SELECT and return the first row as a dict, or None."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def fetch_all(db_path: str, sql: str, params: tuple = ()) -> list[dict]:
    """Execute a SELECT and return all rows as a list of dicts."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def insert(db_path: str, sql: str, params: tuple = ()) -> int:
    """Execute an INSERT and return lastrowid."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(sql, params) as cur:
            lastrowid = cur.lastrowid
        await db.commit()
        return lastrowid


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_all_dbs()
    print("All SQLite databases initialized.")

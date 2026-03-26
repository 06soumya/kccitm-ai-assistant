"""
MySQL connection client for KCCITM AI Assistant.

Provides both async (aiomysql) for FastAPI and sync (pymysql) for CLI/ETL scripts.

Usage (async):
    from db.mysql_client import execute_query
    rows = await execute_query("SELECT * FROM students WHERE roll_no = %s", ("2104920100002",))

Usage (sync, for ETL):
    from db.mysql_client import sync_execute
    rows = sync_execute("SELECT roll_no, jsontext FROM university_marks")
"""

import asyncio
import logging
from typing import Any

import aiomysql
import pymysql
import pymysql.cursors

from config import settings

logger = logging.getLogger(__name__)

# ── Async pool (singleton) ────────────────────────────────────────────────────

_pool: aiomysql.Pool | None = None


async def get_pool() -> aiomysql.Pool:
    """Return the global async connection pool, creating it if needed."""
    global _pool
    if _pool is None:
        _pool = await aiomysql.create_pool(
            host=settings.MYSQL_HOST,
            port=settings.MYSQL_PORT,
            user=settings.MYSQL_USER,
            password=settings.MYSQL_PASSWORD,
            db=settings.MYSQL_DB,
            charset="utf8mb4",
            autocommit=True,
            minsize=2,
            maxsize=10,
        )
        logger.info("MySQL async pool created")
    return _pool


async def execute_query(sql: str, params: tuple = ()) -> list[dict]:
    """Execute a SELECT query and return all rows as a list of dicts."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def execute_one(sql: str, params: tuple = ()) -> dict | None:
    """Execute a SELECT query and return the first row as a dict, or None."""
    rows = await execute_query(sql, params)
    return rows[0] if rows else None


async def execute_write(sql: str, params: tuple = ()) -> int:
    """Execute an INSERT/UPDATE/DELETE and return lastrowid."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            await conn.commit()
            return cur.lastrowid


async def execute_many(sql: str, params_list: list[tuple]) -> None:
    """Batch execute an INSERT/UPDATE statement."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(sql, params_list)
        await conn.commit()


async def close_pool() -> None:
    """Close the global connection pool."""
    global _pool
    if _pool is not None:
        _pool.close()
        await _pool.wait_closed()
        _pool = None
        logger.info("MySQL async pool closed")


# ── Sync wrappers (for CLI / ETL scripts) ────────────────────────────────────

def get_sync_connection() -> pymysql.Connection:
    """Return a new synchronous PyMySQL connection."""
    return pymysql.connect(
        host=settings.MYSQL_HOST,
        port=settings.MYSQL_PORT,
        user=settings.MYSQL_USER,
        password=settings.MYSQL_PASSWORD,
        database=settings.MYSQL_DB,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def sync_execute(sql: str, params: tuple = ()) -> list[dict]:
    """Execute a SELECT and return all rows as dicts (sync, one-shot connection)."""
    conn = get_sync_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
    finally:
        conn.close()


def sync_execute_many(sql: str, params_list: list[tuple], conn: pymysql.Connection | None = None) -> None:
    """
    Batch execute INSERT/UPDATE (sync).

    If conn is provided, uses it (caller manages commit/close).
    Otherwise opens and closes its own connection.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_sync_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(sql, params_list)
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()


def sync_execute_write(sql: str, params: tuple = ()) -> int:
    """Execute a single INSERT/UPDATE/DELETE and return lastrowid (sync)."""
    conn = get_sync_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
        return conn.insert_id()
    finally:
        conn.close()

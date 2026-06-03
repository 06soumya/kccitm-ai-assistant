"""
Eval runner — loads queries.yaml and runs each through the orchestrator,
comparing actual route + numeric answer to expected.

Storage:
  data/eval.db
    eval_runs(id TEXT PK, started_at, finished_at, total, passed,
              failed, errored, status)
    eval_results(run_id, query_id, query_text, expected_route,
                 actual_route, route_match, expected_value,
                 actual_value, value_match, response, error, duration_ms)

Usage:
  from eval.runner import run_eval, get_queries
  report = await run_eval()
"""

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from config import settings
from db.mysql_client import execute_query
from db.sqlite_client import execute, fetch_all, fetch_one

logger = logging.getLogger(__name__)


# ── Paths + schema ────────────────────────────────────────────────────────────

QUERIES_PATH = Path(__file__).parent / "queries.yaml"
EVAL_DB = Path(settings.SESSION_DB).parent / "eval.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS eval_runs (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    total INTEGER DEFAULT 0,
    completed INTEGER DEFAULT 0,
    passed INTEGER DEFAULT 0,
    failed INTEGER DEFAULT 0,
    errored INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running'  -- running | finished | aborted
);

CREATE TABLE IF NOT EXISTS eval_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    query_id TEXT NOT NULL,
    category TEXT,
    query_text TEXT,
    expected_route TEXT,
    actual_route TEXT,
    route_match INTEGER,
    expected_value TEXT,
    actual_value TEXT,
    value_match INTEGER,
    response TEXT,
    error TEXT,
    duration_ms INTEGER,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_eval_results_run ON eval_results(run_id);
"""


async def init_eval_db() -> None:
    """Create eval.db tables if missing. Safe to call repeatedly."""
    EVAL_DB.parent.mkdir(parents=True, exist_ok=True)
    for stmt in _SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            await execute(str(EVAL_DB), stmt)


# ── YAML loading ──────────────────────────────────────────────────────────────

def load_queries() -> dict:
    """Return the parsed queries.yaml content."""
    with open(QUERIES_PATH, "r") as f:
        return yaml.safe_load(f)


def get_queries() -> list[dict]:
    """Just the query list."""
    return load_queries().get("queries", [])


# ── Helpers ───────────────────────────────────────────────────────────────────

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _extract_first_number(text: str, pattern: Optional[str] = None) -> Optional[float]:
    """
    Pull the first numeric token from a natural-language response.

    If `pattern` is given, it should be a regex with one capture group that
    isolates the value (e.g. for queries where the right number isn't the
    first one in the response).
    """
    if not text:
        return None
    if pattern:
        try:
            m = re.search(pattern, text)
            if m:
                return float(m.group(1))
        except (re.error, ValueError, IndexError):
            pass

    m = _NUMBER_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


async def _fetch_ground_truth(sql: str) -> Optional[float]:
    """Run the ground_truth_sql and return its single numeric value."""
    try:
        rows = await execute_query(sql)
    except Exception as exc:
        logger.warning("Ground-truth SQL failed: %s | sql=%s", exc, sql[:120])
        return None
    if not rows:
        return None
    row = rows[0]
    # Pull first column value
    val = next(iter(row.values()))
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _route_matches(actual: str, expected: str, accept: list[str] | None) -> bool:
    """Route check — handles `accept_routes` and the `CACHED (...)` wrapper."""
    if not actual:
        return False
    if accept:
        for r in accept:
            if actual == r or actual.startswith(r):
                return True
    if actual == expected:
        return True
    # `CACHED (semantic)` should also match `CACHED (exact)` etc when expected
    # is the bare route — let's only do exact matches by default and require
    # the user to spell out accept_routes for cache hits.
    return False


@dataclass
class QueryResult:
    query_id: str
    category: str
    query_text: str
    expected_route: str
    actual_route: str
    route_match: bool
    expected_value: Optional[float] = None
    actual_value: Optional[float] = None
    value_match: Optional[bool] = None
    response: str = ""
    error: Optional[str] = None
    duration_ms: int = 0


# ── Runner ────────────────────────────────────────────────────────────────────

async def run_eval(
    orchestrator,
    run_id: Optional[str] = None,
    progress_cb=None,
) -> dict:
    """
    Run every query in queries.yaml through the orchestrator.

    Args:
        orchestrator: an Orchestrator instance (caller provides — usually
            obtained from api.deps.get_orchestrator)
        run_id: optional pre-allocated run id (caller can return it to the
            client before kicking off the background task)
        progress_cb: optional async coroutine `progress_cb(completed, total)`
            invoked after every query

    Returns the final summary dict (also persisted to eval.db).
    """
    await init_eval_db()
    queries = get_queries()
    total = len(queries)
    run_id = run_id or f"run_{uuid.uuid4().hex[:8]}"
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    await execute(
        str(EVAL_DB),
        "INSERT INTO eval_runs (id, started_at, total, status) VALUES (?, ?, ?, 'running')",
        (run_id, started, total),
    )

    passed = failed = errored = 0
    completed = 0

    for q in queries:
        result = await _run_one(orchestrator, q)
        completed += 1

        if result.error:
            errored += 1
        elif result.route_match and (result.value_match is None or result.value_match):
            passed += 1
        else:
            failed += 1

        await execute(
            str(EVAL_DB),
            """INSERT INTO eval_results
               (run_id, query_id, category, query_text, expected_route,
                actual_route, route_match, expected_value, actual_value,
                value_match, response, error, duration_ms, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, result.query_id, result.category, result.query_text,
                result.expected_route, result.actual_route,
                1 if result.route_match else 0,
                json.dumps(result.expected_value) if result.expected_value is not None else None,
                json.dumps(result.actual_value) if result.actual_value is not None else None,
                None if result.value_match is None else (1 if result.value_match else 0),
                (result.response or "")[:2000],
                result.error,
                result.duration_ms,
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            ),
        )

        await execute(
            str(EVAL_DB),
            "UPDATE eval_runs SET completed = ?, passed = ?, failed = ?, errored = ? WHERE id = ?",
            (completed, passed, failed, errored, run_id),
        )

        if progress_cb:
            try:
                await progress_cb(completed, total)
            except Exception:
                pass

    finished = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    await execute(
        str(EVAL_DB),
        "UPDATE eval_runs SET finished_at = ?, status = 'finished' WHERE id = ?",
        (finished, run_id),
    )

    return {
        "run_id": run_id,
        "total": total,
        "passed": passed,
        "failed": failed,
        "errored": errored,
        "pass_rate": round(passed / total, 4) if total else 0.0,
    }


async def _run_one(orchestrator, q: dict) -> QueryResult:
    """Run a single query and judge it."""
    qid = q.get("id", "?")
    category = q.get("category", "")
    text = q.get("query", "")
    expected_route = q.get("expected_route", "")
    accept_routes = q.get("accept_routes")
    ground_truth_sql = q.get("ground_truth_sql")
    tolerance = float(q.get("tolerance", 0.1))
    extract_pattern = q.get("extract_pattern")
    expected_substring = q.get("expected_substring")

    t0 = time.time()
    try:
        resp = await orchestrator.process_query(query=text)
    except Exception as exc:
        return QueryResult(
            query_id=qid, category=category, query_text=text,
            expected_route=expected_route, actual_route="",
            route_match=False, error=f"orchestrator raised: {exc}",
            duration_ms=int((time.time() - t0) * 1000),
        )

    duration_ms = int((time.time() - t0) * 1000)
    actual_route = resp.route_used or ""
    route_match = _route_matches(actual_route, expected_route, accept_routes)

    expected_value = None
    actual_value = None
    value_match = None

    # Numeric comparison for aggregate queries with ground_truth_sql
    if ground_truth_sql and resp.success:
        expected_value = await _fetch_ground_truth(ground_truth_sql)
        actual_value = _extract_first_number(resp.response or "", extract_pattern)
        if expected_value is None or actual_value is None:
            value_match = False
        else:
            value_match = abs(actual_value - expected_value) <= tolerance

    # Substring check for student-lookup queries
    if expected_substring and resp.success:
        ok = expected_substring.lower() in (resp.response or "").lower()
        # If the route matches and substring is present, we count value_match as True
        value_match = ok if value_match is None else (value_match and ok)

    return QueryResult(
        query_id=qid, category=category, query_text=text,
        expected_route=expected_route, actual_route=actual_route,
        route_match=route_match,
        expected_value=expected_value, actual_value=actual_value,
        value_match=value_match,
        response=(resp.response or "")[:2000],
        error=resp.error if not resp.success else None,
        duration_ms=duration_ms,
    )


# ── Run history ───────────────────────────────────────────────────────────────

async def list_runs(limit: int = 20) -> list[dict]:
    await init_eval_db()
    return await fetch_all(
        str(EVAL_DB),
        "SELECT * FROM eval_runs ORDER BY started_at DESC LIMIT ?",
        (limit,),
    )


async def get_run(run_id: str) -> dict | None:
    await init_eval_db()
    run = await fetch_one(
        str(EVAL_DB), "SELECT * FROM eval_runs WHERE id = ?", (run_id,)
    )
    if not run:
        return None
    results = await fetch_all(
        str(EVAL_DB),
        "SELECT * FROM eval_results WHERE run_id = ? ORDER BY id",
        (run_id,),
    )
    run["results"] = results
    return run


async def get_latest_run() -> dict | None:
    await init_eval_db()
    row = await fetch_one(
        str(EVAL_DB),
        "SELECT id FROM eval_runs ORDER BY started_at DESC LIMIT 1",
    )
    if not row:
        return None
    return await get_run(row["id"])

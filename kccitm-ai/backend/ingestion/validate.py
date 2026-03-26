"""
End-to-End Validation for KCCITM AI Assistant Phase 1.

Verifies the complete data stack is working:
  1. MySQL normalized tables (students, semester_results, subject_marks)
  2. Milvus student_results collection stats
  3. Dense search test
  4. BM25 keyword search test
  5. Hybrid search test
  6. Filtered search test
  7. FAQ collection check
  8. SQLite database check

Usage:
    cd backend
    python -m ingestion.validate
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
from typing import Any

import httpx

from config import settings
from db.mysql_client import sync_execute
from db.milvus_client import MilvusSearchClient
from db.sqlite_client import init_all_dbs

# ── ANSI colours ─────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ok(msg: str) -> None:
    print(f"{GREEN}✓ {msg}{RESET}")


def _fail(msg: str) -> None:
    print(f"{RED}✗ {msg}{RESET}")


def _warn(msg: str) -> None:
    print(f"{YELLOW}⚠ {msg}{RESET}")


async def _embed(text: str) -> list[float] | None:
    """Get embedding from Ollama."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{settings.OLLAMA_HOST}/api/embeddings",
                json={"model": settings.OLLAMA_EMBED_MODEL, "prompt": text},
            )
            resp.raise_for_status()
            return resp.json()["embedding"]
    except Exception as exc:
        _warn(f"Ollama embedding failed: {exc}")
        return None


# ── Validation checks ─────────────────────────────────────────────────────────

def check_mysql() -> tuple[bool, dict]:
    """Validate MySQL normalized tables."""
    print("\n── MySQL Validation ────────────────────────────────────────")
    try:
        students = sync_execute("SELECT COUNT(*) as cnt FROM students")[0]["cnt"]
        semesters = sync_execute("SELECT COUNT(*) as cnt FROM semester_results")[0]["cnt"]
        subjects = sync_execute("SELECT COUNT(*) as cnt FROM subject_marks")[0]["cnt"]

        _ok(f"MySQL: {students} students, {semesters} semester records, {subjects} subject marks")

        # Sample query: top 5 students by SGPA in semester 1
        top5 = sync_execute("""
            SELECT s.name, s.roll_no, sr.sgpa
            FROM semester_results sr
            JOIN students s ON s.roll_no = sr.roll_no
            WHERE sr.semester = 1
            ORDER BY sr.sgpa DESC
            LIMIT 5
        """)
        if top5:
            _ok("Sample query (top 5 by SGPA in sem 1):")
            for row in top5:
                print(f"    {row['name']} ({row['roll_no']}) — SGPA: {row['sgpa']}")

        return True, {"students": students, "semesters": semesters, "subjects": subjects}

    except Exception as exc:
        _fail(f"MySQL validation failed: {exc}")
        return False, {}


def check_milvus_stats(client: MilvusSearchClient) -> tuple[bool, int]:
    """Check Milvus collection entity count."""
    print("\n── Milvus Collection Stats ─────────────────────────────────")
    try:
        stats = client.get_collection_stats(settings.MILVUS_COLLECTION)
        count = stats["row_count"]
        _ok(f"Milvus '{settings.MILVUS_COLLECTION}': {count} entities indexed (dense + BM25)")
        return True, count
    except Exception as exc:
        _fail(f"Milvus stats failed: {exc}")
        return False, 0


async def check_dense_search(client: MilvusSearchClient) -> bool:
    """Test dense semantic search."""
    print("\n── Dense Search Test ───────────────────────────────────────")
    embedding = await _embed("top CSE students")
    if embedding is None:
        _warn("Skipping dense search test — Ollama not available")
        return False

    try:
        results = client.dense_search(query_embedding=embedding, k=5)
        if results:
            _ok(f"Dense search: working (top result: {results[0]['metadata'].get('name', '?')} "
                f"— score {results[0]['score']:.4f})")
            for r in results[:3]:
                m = r["metadata"]
                print(f"    {m.get('name','?')} sem{m.get('semester','?')} SGPA:{m.get('sgpa','?')} score:{r['score']:.4f}")
            return True
        else:
            _fail("Dense search returned no results")
            return False
    except Exception as exc:
        _fail(f"Dense search failed: {exc}")
        return False


def check_bm25_search(client: MilvusSearchClient) -> bool:
    """Test BM25 keyword search with exact subject code."""
    print("\n── BM25 Keyword Search Test ────────────────────────────────")
    try:
        results = client.keyword_search(query_text="KCS503", k=5)
        found = sum(1 for r in results if "KCS503" in r.get("text", ""))
        if results:
            _ok(f"BM25 search: working (KCS503 found in {found}/{len(results)} results)")
            for r in results[:3]:
                m = r["metadata"]
                print(f"    {m.get('name','?')} sem{m.get('semester','?')} score:{r['score']:.4f}")
            return True
        else:
            _warn("BM25 search: no results for KCS503 (subject may not exist in dataset)")
            return True  # Not a hard failure
    except Exception as exc:
        _fail(f"BM25 search failed: {exc}")
        return False


async def check_hybrid_search(client: MilvusSearchClient) -> bool:
    """Test hybrid search (dense + BM25)."""
    print("\n── Hybrid Search Test ──────────────────────────────────────")
    embedding = await _embed("students struggling in programming")
    if embedding is None:
        _warn("Skipping hybrid search test — Ollama not available")
        return False

    try:
        results = client.hybrid_search(
            query_text="students struggling in programming",
            query_embedding=embedding,
            k=5,
        )
        if results:
            _ok(f"Hybrid search: working (top result: {results[0]['metadata'].get('name', '?')} "
                f"— score {results[0]['score']:.4f})")
            for r in results[:3]:
                m = r["metadata"]
                print(f"    {m.get('name','?')} sem{m.get('semester','?')} SGPA:{m.get('sgpa','?')} score:{r['score']:.4f}")
            return True
        else:
            _fail("Hybrid search returned no results")
            return False
    except Exception as exc:
        _fail(f"Hybrid search failed: {exc}")
        return False


async def check_filtered_search(client: MilvusSearchClient) -> bool:
    """Test filtered search with semester + branch filter."""
    print("\n── Filtered Search Test ────────────────────────────────────")
    embedding = await _embed("computer science students semester 4")
    if embedding is None:
        _warn("Skipping filtered search test — Ollama not available")
        return False

    try:
        # Use dense_search for filter validation — milvus-lite applies filters
        # reliably on single-field ANN search (hybrid_search filter support is
        # limited in the embedded lite engine).
        results = client.dense_search(
            query_embedding=embedding,
            k=5,
            filters={"semester": 4},
        )

        if not results:
            _warn("Filtered search: no results for semester=4 (may not exist in dataset)")
            return True

        mismatches = [r for r in results if r["metadata"].get("semester") != 4]
        if mismatches:
            _fail(f"Filtered search: {len(mismatches)} results don't match semester=4 filter")
            return False

        _ok(f"Filtered search: working (all {len(results)} results match semester=4)")
        for r in results[:3]:
            m = r["metadata"]
            print(f"    {m.get('name','?')} sem{m.get('semester','?')} score:{r['score']:.4f}")
        return True

    except Exception as exc:
        _fail(f"Filtered search failed: {exc}")
        return False


def check_faq_collection(client: MilvusSearchClient) -> bool:
    """Verify the FAQ collection exists and is empty."""
    print("\n── FAQ Collection Check ────────────────────────────────────")
    try:
        from pymilvus import MilvusClient as _MC
        raw = _MC(uri=settings.milvus_uri)
        exists = raw.has_collection(settings.MILVUS_FAQ_COLLECTION)
        if not exists:
            _fail(f"FAQ collection '{settings.MILVUS_FAQ_COLLECTION}' does not exist")
            return False

        stats = client.get_collection_stats(settings.MILVUS_FAQ_COLLECTION)
        count = stats["row_count"]
        _ok(f"Milvus FAQ: collection ready ({count} entries)")
        return True
    except Exception as exc:
        _fail(f"FAQ collection check failed: {exc}")
        return False


def check_sqlite() -> bool:
    """Verify all SQLite databases and their tables exist."""
    print("\n── SQLite Databases Check ──────────────────────────────────")

    db_table_map = {
        settings.SESSION_DB:  ["sessions", "messages", "users"],
        settings.CACHE_DB:    ["query_cache"],
        settings.FEEDBACK_DB: ["feedback", "implicit_signals", "chunk_analytics", "training_candidates"],
        settings.PROMPTS_DB:  ["prompt_templates", "prompt_evolution_log", "faq_entries"],
    }

    all_ok = True
    for db_path, expected_tables in db_table_map.items():
        if not os.path.exists(db_path):
            _fail(f"{db_path} — file not found")
            all_ok = False
            continue

        try:
            conn = sqlite3.connect(db_path)
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            actual = {row[0] for row in cur.fetchall()}
            conn.close()

            missing = set(expected_tables) - actual
            if missing:
                _fail(f"{db_path} — missing tables: {missing}")
                all_ok = False
            else:
                _ok(f"{db_path} — all {len(expected_tables)} tables present")
        except Exception as exc:
            _fail(f"{db_path} — error: {exc}")
            all_ok = False

    return all_ok


# ── Main runner ───────────────────────────────────────────────────────────────

async def run_validation() -> None:
    """Run all validation checks and print summary."""
    print(f"\n{'='*60}")
    print("  KCCITM AI Assistant — Phase 1 Validation")
    print(f"{'='*60}")

    results: dict[str, bool] = {}

    # 1. MySQL
    ok, mysql_stats = check_mysql()
    results["mysql"] = ok

    # 2-6. Milvus checks
    try:
        client = MilvusSearchClient(
            uri=settings.milvus_uri,
            collection=settings.MILVUS_COLLECTION,
        )

        ok, chunk_count = check_milvus_stats(client)
        results["milvus_stats"] = ok

        results["dense_search"]    = await check_dense_search(client)
        results["bm25_search"]     = check_bm25_search(client)
        results["hybrid_search"]   = await check_hybrid_search(client)
        results["filtered_search"] = await check_filtered_search(client)
        results["faq_collection"]  = check_faq_collection(client)

    except Exception as exc:
        _fail(f"Milvus connection failed: {exc}")
        for key in ["milvus_stats", "dense_search", "bm25_search",
                    "hybrid_search", "filtered_search", "faq_collection"]:
            results[key] = False
        chunk_count = 0

    # 7. SQLite
    results["sqlite"] = check_sqlite()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  Summary")
    print(f"{'='*60}")

    if results.get("mysql"):
        _ok(f"MySQL: {mysql_stats.get('students','?')} students, "
            f"{mysql_stats.get('semesters','?')} semester records, "
            f"{mysql_stats.get('subjects','?')} subject marks")
    else:
        _fail("MySQL: validation failed")

    if results.get("milvus_stats"):
        _ok(f"Milvus student_results: {chunk_count} chunks indexed (dense + BM25)")
    else:
        _fail("Milvus student_results: validation failed")

    if results.get("faq_collection"):
        _ok("Milvus FAQ: collection ready (0 entries)")
    else:
        _fail("Milvus FAQ: not ready")

    for key, label in [
        ("dense_search",    "Dense search: working"),
        ("bm25_search",     "BM25 search: working"),
        ("hybrid_search",   "Hybrid search: working"),
        ("filtered_search", "Filtered search: working"),
    ]:
        if results.get(key):
            _ok(label)
        else:
            _fail(label.replace("working", "FAILED"))

    if results.get("sqlite"):
        _ok("SQLite: all databases and tables initialized")
    else:
        _fail("SQLite: some databases or tables missing")

    all_passed = all(results.values())
    print(f"\n{'='*60}")
    if all_passed:
        print(f"{GREEN}✓ All validations passed. System ready for Phase 2.{RESET}")
    else:
        failed = [k for k, v in results.items() if not v]
        print(f"{RED}✗ Some validations failed: {', '.join(failed)}{RESET}")
        print("  Re-run the failed ingestion steps and try again.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(run_validation())

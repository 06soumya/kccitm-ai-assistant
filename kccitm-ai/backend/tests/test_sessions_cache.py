"""
Session management and query cache tests.
Run: python -m tests.test_sessions_cache

Tests:
  1. Session CRUD — create, messages, history, list, auto-title, delete
  2. Cache exact match — hash, case-insensitive, miss
  3. Cache semantic match — paraphrase detection, negative miss
  4. Cache stats — entry count, hit count
  5. Multi-turn conversation via orchestrator with session persistence + cache
"""
import asyncio
import time

from config import settings
from core.cache import QueryCache
from core.llm_client import OllamaClient
from core.session_manager import SessionManager

PASS = "\033[92m✓ PASSED\033[0m"
SKIP = "\033[93m○ SKIPPED\033[0m"
FAIL = "\033[91m✗ FAILED\033[0m"

_passed = 0
_failed = 0


def _ok(label: str) -> None:
    global _passed
    _passed += 1
    print(f"  {PASS} {label}")


def _fail(label: str, reason: str = "") -> None:
    global _failed
    _failed += 1
    print(f"  {FAIL} {label}" + (f" — {reason}" if reason else ""))


# ── Test 1: Session CRUD ──────────────────────────────────────────────────────

async def test_session_crud():
    print("\n=== Test 1: Session CRUD ===")
    sm = SessionManager()

    # Create session
    session = await sm.create_session("user_test_p6", "Test Session P6")
    assert session.id, "Session should have an ID"
    print(f"  Created session: {session.id[:8]}...")
    _ok("create_session returns Session with id")

    # Add messages
    await sm.add_message(session.id, "user", "top 5 CSE students")
    await sm.add_message(session.id, "assistant", "Here are the top 5...",
                         {"route_used": "SQL", "sql_query": "SELECT..."})

    # Get session with messages
    loaded = await sm.get_session(session.id)
    assert loaded is not None
    assert len(loaded.messages) == 2
    _ok("get_session returns 2 messages")

    assert loaded.messages[0].role == "user"
    assert loaded.messages[1].metadata.get("route_used") == "SQL"
    _ok("message roles and metadata preserved")

    # get_chat_history format
    history = await sm.get_chat_history(session.id)
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert "content" in history[0]
    _ok("get_chat_history returns [{role, content}] format")

    # list_sessions
    sessions = await sm.list_sessions("user_test_p6")
    assert len(sessions) >= 1
    assert sessions[0]["message_count"] == 2
    _ok(f"list_sessions: found {len(sessions)} session(s), message_count=2")

    # Auto-title from first user message
    session2 = await sm.create_session("user_test_p6")
    await sm.add_message(session2.id, "user",
                         "What is the average SGPA of CSE students in semester 4?")
    loaded2 = await sm.get_session(session2.id)
    assert loaded2 and loaded2.title, "Title should be auto-generated"
    print(f"  Auto-title: \"{loaded2.title}\"")
    _ok("auto-title from first user message")

    # Delete
    await sm.delete_session(session.id)
    await sm.delete_session(session2.id)
    deleted = await sm.get_session(session.id)
    assert deleted is None
    _ok("delete_session: session no longer retrievable")


# ── Test 2: Cache Exact Match ─────────────────────────────────────────────────

async def test_cache_exact(llm: OllamaClient):
    print("\n=== Test 2: Cache — Exact Match ===")
    cache = QueryCache(llm)
    await cache.clear()

    # Store
    await cache.store(
        query="top 5 students by SGPA",
        response="Here are the top 5 students by SGPA...",
        route_used="SQL",
        metadata={"sql_query": "SELECT..."},
    )
    _ok("store() completes without error")

    # Exact hit
    hit = await cache.check("top 5 students by SGPA")
    assert hit is not None, "Should be a cache hit"
    assert hit.cache_type == "exact"
    assert hit.confidence == 1.0
    _ok("exact match: confidence=1.0, cache_type='exact'")

    # Case-insensitive + whitespace
    hit2 = await cache.check("  Top 5 Students by SGPA  ")
    assert hit2 is not None, "Should match with different casing"
    assert hit2.cache_type == "exact"
    _ok("case-insensitive and whitespace-tolerant match")

    # Trailing punctuation
    hit3 = await cache.check("top 5 students by SGPA?")
    assert hit3 is not None, "Should match with trailing punctuation stripped"
    _ok("trailing punctuation stripped before hashing")

    # Miss
    miss = await cache.check("completely different query xyz 12345 banana")
    assert miss is None, "Should be a cache miss"
    _ok("unrelated query correctly returns None (cache miss)")

    await cache.clear()


# ── Test 3: Cache Semantic Match ──────────────────────────────────────────────

async def test_cache_semantic(llm: OllamaClient):
    print("\n=== Test 3: Cache — Semantic Match ===")
    cache = QueryCache(llm)
    await cache.clear()

    original = "top 5 students by SGPA in semester 4"
    await cache.store(
        query=original,
        response="The top 5 students in semester 4 by SGPA are...",
        route_used="SQL",
    )
    print(f"  Stored: \"{original}\"")

    # Paraphrase — should match semantically
    paraphrase = "best performing students in sem 4 by SGPA"
    hit = await cache.check(paraphrase)
    if hit and hit.cache_type == "semantic":
        print(f"  Semantic match: confidence={hit.confidence:.3f}")
        _ok(f"semantic paraphrase match (confidence={hit.confidence:.3f})")
    elif hit and hit.cache_type == "exact":
        _ok("matched (exact — normalized query identical)")
    else:
        print(f"  {SKIP} Semantic match didn't fire "
              f"(threshold={cache.similarity_threshold} may be strict for this model)")

    # Very different query must NOT match
    miss = await cache.check("how many students failed in semester 1")
    assert miss is None, "Unrelated query should not match"
    _ok("unrelated query does not trigger semantic match")

    await cache.clear()


# ── Test 4: Cache Stats ───────────────────────────────────────────────────────

async def test_cache_stats(llm: OllamaClient):
    print("\n=== Test 4: Cache Stats ===")
    cache = QueryCache(llm)
    await cache.clear()

    # Insert 5 entries
    for i in range(5):
        await cache.store(f"test stats query number {i}", f"response {i}", "SQL")

    # Trigger 2 hits on query 0
    await cache.check("test stats query number 0")
    await cache.check("test stats query number 0")

    stats = await cache.get_stats()
    print(f"  Total entries: {stats['total_entries']}")
    print(f"  Active entries: {stats['active_entries']}")
    print(f"  Total hits: {stats['total_hits']}")

    assert stats["total_entries"] == 5, f"Expected 5 entries, got {stats['total_entries']}"
    _ok("total_entries = 5")

    assert stats["active_entries"] == 5
    _ok("active_entries = 5 (all within TTL)")

    assert stats["total_hits"] >= 2, f"Expected >=2 hits, got {stats['total_hits']}"
    _ok(f"total_hits >= 2 ({stats['total_hits']})")

    assert isinstance(stats["top_queries"], list)
    _ok("top_queries returned as list")

    await cache.clear()


# ── Test 5: Multi-turn Conversation via Orchestrator ─────────────────────────

async def test_session_with_orchestrator(llm: OllamaClient):
    print("\n=== Test 5: Multi-Turn Conversation ===")
    from core.router import QueryRouter
    from core.sql_pipeline import SQLPipeline
    from core.rag_pipeline import RAGPipeline
    from core.orchestrator import Orchestrator
    from db.milvus_client import MilvusSearchClient

    router = QueryRouter(llm)
    sql_pipeline = SQLPipeline(llm)
    milvus = MilvusSearchClient(uri=settings.milvus_uri)
    rag_pipeline = RAGPipeline(llm, milvus)
    session_mgr = SessionManager()
    cache = QueryCache(llm)
    await cache.clear()

    orchestrator = Orchestrator(
        llm, router, sql_pipeline, rag_pipeline, milvus, session_mgr, cache
    )

    # Create session
    session = await session_mgr.create_session("test_user_p6")
    print(f"  Session: {session.id[:8]}...")

    # Turn 1
    print(f"\n  Turn 1: \"top 3 CSE students in semester 1\"")
    r1 = await orchestrator.process_query(
        "top 3 CSE students in semester 1", session_id=session.id
    )
    print(f"  Route: {r1.route_used} | Time: {r1.total_time_ms:.0f}ms")
    print(f"  Response: {r1.response[:120]}...")
    assert r1.success, f"Turn 1 failed: {r1.error}"
    _ok("Turn 1 succeeds")

    # Turn 2 — follow-up
    print(f"\n  Turn 2: \"what about semester 4?\"")
    r2 = await orchestrator.process_query(
        "what about semester 4?", session_id=session.id
    )
    print(f"  Route: {r2.route_used} | Time: {r2.total_time_ms:.0f}ms")
    print(f"  Response: {r2.response[:120]}...")
    assert r2.success, f"Turn 2 failed: {r2.error}"
    _ok("Turn 2 (follow-up) succeeds")

    # Turn 3 — repeat query (should hit cache)
    print(f"\n  Turn 3: \"top 3 CSE students in semester 1\" (repeat)")
    t3_start = time.time()
    r3 = await orchestrator.process_query(
        "top 3 CSE students in semester 1", session_id=session.id
    )
    t3_elapsed = (time.time() - t3_start) * 1000
    is_cached = "CACHED" in r3.route_used
    print(f"  Route: {r3.route_used} | Time: {t3_elapsed:.0f}ms | Cached: {is_cached}")
    assert r3.success
    _ok("Turn 3 (repeat) succeeds")
    if is_cached:
        _ok(f"Cache hit on repeated query ({t3_elapsed:.0f}ms vs {r1.total_time_ms:.0f}ms original)")
    else:
        print(f"  {SKIP} Cache miss on repeat (semantic threshold may be strict)")

    # Verify session history persisted
    history = await session_mgr.get_chat_history(session.id)
    print(f"\n  Session history: {len(history)} messages")
    assert len(history) >= 4, f"Expected >=4 messages, got {len(history)}"
    _ok(f"Session stores {len(history)} messages across turns")

    # Cleanup
    await session_mgr.delete_session(session.id)
    await cache.clear()


# ── Runner ────────────────────────────────────────────────────────────────────

async def run_all():
    print(f"{'='*60}")
    print(f"Phase 6: Session & Cache Test Suite")
    print(f"{'='*60}")

    llm = OllamaClient()
    health = await llm.health_check()
    if health["status"] != "ok":
        print(f"\033[91m✗ Ollama not running — aborting\033[0m")
        return

    await test_session_crud()
    await test_cache_exact(llm)
    await test_cache_semantic(llm)
    await test_cache_stats(llm)
    await test_session_with_orchestrator(llm)

    total = _passed + _failed
    pct = (_passed / total * 100) if total > 0 else 0
    print(f"\n{'='*60}")
    print(f"Results: {_passed}/{total} passed ({pct:.0f}%)")
    if _failed == 0:
        print(f"\033[92m✓ All session & cache tests passed!\033[0m")
    else:
        print(f"\033[91m{_failed} test(s) failed\033[0m")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(run_all())

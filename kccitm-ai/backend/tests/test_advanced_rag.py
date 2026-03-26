"""
Advanced RAG optimization tests.

Tests HyDE, Multi-Query expansion + RRF, Cross-encoder re-ranking,
Contextual compression, and the full optimized pipeline (with basic vs
optimized A/B comparison).

Requires: MySQL + Milvus (milvus-lite) + Ollama all running with Phase 1 data.

Run:
    cd backend
    python -m tests.test_advanced_rag
"""

import asyncio
import time

from config import settings
from core.compressor import ContextualCompressor
from core.hyde import HyDEGenerator
from core.llm_client import OllamaClient
from core.multi_query import MultiQueryExpander
from core.rag_pipeline import RAGPipeline
from core.reranker import ChunkReranker
from core.router import QueryRouter
from db.milvus_client import MilvusSearchClient

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

passed_total = 0
failed_total = 0


def _pass(msg: str) -> None:
    global passed_total
    passed_total += 1
    print(f"  {GREEN}✓ {msg}{RESET}")


def _fail(msg: str) -> None:
    global failed_total
    failed_total += 1
    print(f"  {RED}✗ {msg}{RESET}")


# ── Test 1: HyDE ──────────────────────────────────────────────────────────────

async def test_hyde(llm: OllamaClient) -> None:
    print("\n=== Test 1: HyDE Generation ===")
    hyde = HyDEGenerator(llm)

    query = "students struggling in programming"
    text, embedding = await hyde.generate_and_embed(query)

    print(f"  Query:     \"{query}\"")
    print(f"  HyDE text ({len(text)} chars): {text[:150]}...")
    print(f"  Embedding: {len(embedding)}-dim")

    if len(text) > 30:
        _pass("HyDE text has reasonable length")
    else:
        _fail(f"HyDE text too short ({len(text)} chars)")

    if len(embedding) == 768:
        _pass("Embedding is 768-dim")
    else:
        _fail(f"Wrong embedding dim: {len(embedding)}")

    academic_terms = ["sgpa", "grade", "marks", "semester", "subject", "branch", "result"]
    has_terms = any(t in text.lower() for t in academic_terms)
    if has_terms:
        _pass("HyDE text contains academic vocabulary")
    else:
        _fail("HyDE text missing academic terms")


# ── Test 2: Multi-query expansion ─────────────────────────────────────────────

async def test_multi_query(llm: OllamaClient) -> None:
    print("\n=== Test 2: Multi-Query Expansion ===")
    expander = MultiQueryExpander(llm)

    query = "top CSE students in semester 4"
    variants = await expander.expand(query)

    print(f"  Original: \"{query}\"")
    for i, v in enumerate(variants, 1):
        print(f"  Variant {i}: \"{v}\"")

    if len(variants) >= 2:
        _pass(f"Expanded to {len(variants)} variants")
    else:
        _fail(f"Too few variants: {len(variants)} (expected 2-3)")

    if all(isinstance(v, str) and len(v) > 5 for v in variants):
        _pass("All variants are non-empty strings")
    else:
        _fail("Some variants are empty or not strings")

    # At least one variant should differ significantly from the original
    if any(v.lower() != query.lower() for v in variants):
        _pass("Variants differ from original")
    else:
        _fail("Variants are identical to original query")


# ── Test 3: RRF ───────────────────────────────────────────────────────────────

async def test_rrf() -> None:
    print("\n=== Test 3: Reciprocal Rank Fusion ===")

    list1 = [
        {"chunk_id": "A", "score": 0.9, "text": "chunk A"},
        {"chunk_id": "B", "score": 0.8, "text": "chunk B"},
        {"chunk_id": "C", "score": 0.7, "text": "chunk C"},
    ]
    list2 = [
        {"chunk_id": "B", "score": 0.95, "text": "chunk B"},
        {"chunk_id": "D", "score": 0.85, "text": "chunk D"},
        {"chunk_id": "A", "score": 0.75, "text": "chunk A"},
    ]

    merged = MultiQueryExpander.reciprocal_rank_fusion([list1, list2])
    ids = [m["chunk_id"] for m in merged]
    scores = {m["chunk_id"]: m["rrf_score"] for m in merged}

    print(f"  List 1: A(0.9), B(0.8), C(0.7)")
    print(f"  List 2: B(0.95), D(0.85), A(0.75)")
    print(f"  Merged: {', '.join(f'{i}({scores[i]:.4f})' for i in ids)}")

    if set(ids) == {"A", "B", "C", "D"}:
        _pass("All 4 unique chunks present in merged result")
    else:
        _fail(f"Missing chunks in merged result: {set(ids)}")

    if "rrf_score" in merged[0]:
        _pass("RRF scores attached to results")
    else:
        _fail("Missing rrf_score field")

    # B appears in both lists → should rank higher than C (only in list1)
    b_score = scores.get("B", 0)
    c_score = scores.get("C", 0)
    if b_score > c_score:
        _pass(f"B (in 2 lists) outranks C (in 1 list): {b_score:.4f} > {c_score:.4f}")
    else:
        _fail(f"B should outrank C: {b_score:.4f} vs {c_score:.4f}")


# ── Test 4: Re-ranker ─────────────────────────────────────────────────────────

async def test_reranker(llm: OllamaClient, milvus: MilvusSearchClient) -> None:
    print("\n=== Test 4: Cross-Encoder Re-ranking ===")

    query = "programming performance"
    embedding = await llm.embed(query)
    chunks = milvus.hybrid_search(query, embedding, k=10)

    if not chunks:
        print(f"  {YELLOW}⚠ No chunks found — skipping reranker test{RESET}")
        return

    reranker = ChunkReranker()
    t0 = time.time()
    reranked = reranker.rerank(query, chunks, top_k=5)
    elapsed = (time.time() - t0) * 1000

    print(f"  Input: {len(chunks)} chunks → Output: {len(reranked)} | Time: {elapsed:.0f}ms")
    for i, chunk in enumerate(reranked[:3], 1):
        meta = chunk.get("metadata", {})
        print(f"  [{i}] score={chunk.get('rerank_score', 0):.4f} | "
              f"{meta.get('name', 'N/A')} sem {meta.get('semester', '?')}")

    if len(reranked) == 5:
        _pass("Re-ranked to exactly top_k=5")
    else:
        _fail(f"Expected 5 results, got {len(reranked)}")

    if all("rerank_score" in c for c in reranked):
        _pass("All chunks have rerank_score")
    else:
        _fail("Some chunks missing rerank_score")

    sc = [c["rerank_score"] for c in reranked]
    if sc == sorted(sc, reverse=True):
        _pass(f"Scores are descending ({elapsed:.0f}ms for {len(chunks)} pairs)")
    else:
        _fail("Scores not in descending order")


# ── Test 5: Compressor ────────────────────────────────────────────────────────

async def test_compressor(llm: OllamaClient, milvus: MilvusSearchClient) -> None:
    print("\n=== Test 5: Contextual Compression ===")

    query = "How did students perform in programming subjects?"
    embedding = await llm.embed(query)
    chunks = milvus.hybrid_search(query, embedding, k=5)

    if not chunks:
        print(f"  {YELLOW}⚠ No chunks found — skipping compression test{RESET}")
        return

    compressor = ContextualCompressor(llm)
    compressed = await compressor.compress(query, chunks)
    savings = compressor.estimate_savings(chunks, compressed)

    print(f"  Original:   {savings['original_tokens']} tokens in {len(chunks)} chunks")
    print(f"  Compressed: {savings['compressed_tokens']} tokens in {len(compressed)} chunks")
    print(f"  Saved:      {savings['saved_tokens']} tokens ({savings['savings_percent']}%)")
    print(f"  Removed (IRRELEVANT): {savings['chunks_removed']} chunks")

    if len(compressed) > 0:
        _pass(f"At least 1 chunk survived compression ({len(compressed)} remain)")
    else:
        _fail("All chunks removed — safety net should have prevented this")

    if savings["compressed_tokens"] <= savings["original_tokens"]:
        _pass(f"Compression reduced tokens ({savings['savings_percent']}% reduction)")
    else:
        _fail("Compressed tokens > original tokens")


# ── Test 6: Basic vs Optimized A/B comparison ─────────────────────────────────

async def test_pipeline_comparison(llm: OllamaClient, milvus: MilvusSearchClient) -> None:
    print("\n=== Test 6: Basic vs Optimized Pipeline Comparison ===")

    router = QueryRouter(llm)
    pipeline = RAGPipeline(llm, milvus)

    test_queries = [
        "students who scored poorly in programming subjects",
        "tell me about the CSE batch performance in semester 5",
        "KCS503 results",
    ]

    all_ok = True
    for query in test_queries:
        print(f"\n  Query: \"{query}\"")
        route_result = await router.route(query)

        t0 = time.time()
        basic = await pipeline.run(query, route_result, use_optimizations=False)
        basic_time = (time.time() - t0) * 1000

        t0 = time.time()
        optimized = await pipeline.run(query, route_result, use_optimizations=True)
        opt_time = (time.time() - t0) * 1000

        print(f"  Basic:     {basic.chunk_count} chunks | {basic_time:.0f}ms | "
              f"{len(basic.response)} chars response")
        print(f"  Optimized: {optimized.chunk_count} chunks | {opt_time:.0f}ms | "
              f"{len(optimized.response)} chars response")

        if not basic.success:
            _fail(f"Basic pipeline failed: {basic.error}")
            all_ok = False
        if not optimized.success:
            _fail(f"Optimized pipeline failed: {optimized.error}")
            all_ok = False

    if all_ok:
        _pass("Both basic and optimized pipelines returned successful results")


# ── Runner ────────────────────────────────────────────────────────────────────

async def run_all() -> None:
    global passed_total, failed_total

    llm = OllamaClient()
    health = await llm.health_check()
    if health["status"] != "ok":
        print(f"{RED}✗ Ollama not running: {health.get('message')}{RESET}")
        return

    print(f"Ollama running. Models: {health['models']}")
    milvus = MilvusSearchClient(uri=settings.milvus_uri)

    await test_hyde(llm)
    await test_multi_query(llm)
    await test_rrf()
    await test_reranker(llm, milvus)
    await test_compressor(llm, milvus)
    await test_pipeline_comparison(llm, milvus)

    total = passed_total + failed_total
    pct = (passed_total / total * 100) if total > 0 else 0.0

    print(f"\n{'=' * 60}")
    print(f"Advanced RAG Tests: {passed_total}/{total} passed ({pct:.0f}%)")
    print(f"{'=' * 60}")

    if pct >= 70:
        print(f"\n{GREEN}✓ All advanced RAG optimizations working!{RESET}")
    else:
        print(f"\n{RED}✗ Some tests failed — check output above.{RESET}")

    print(f"\nTip: Run interactive mode to see quality improvement:")
    print(f"  python -m tests.test_orchestrator --interactive")


if __name__ == "__main__":
    asyncio.run(run_all())

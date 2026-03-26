"""
Orchestrator end-to-end test suite.

Requires: MySQL + Milvus (milvus-lite) + Ollama all running with data from Phase 1.

Run:
    cd backend
    python -m tests.test_orchestrator              # Automated tests
    python -m tests.test_orchestrator --interactive # Interactive chat mode
"""

import asyncio
import sys
import time

from config import settings
from core.llm_client import OllamaClient
from core.orchestrator import Orchestrator
from core.rag_pipeline import RAGPipeline
from core.router import QueryRouter
from core.sql_pipeline import SQLPipeline
from db.milvus_client import MilvusSearchClient

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
CYAN = "\033[96m"
WHITE = "\033[97m"
RESET = "\033[0m"

# ── Automated test cases ───────────────────────────────────────────────────────
# Format: (query, expected_route_prefix, validate_fn, description)
# validate_fn receives a QueryResponse and returns True/False

AUTOMATED_TESTS = [
    (
        "top 3 students by SGPA in semester 1",
        "SQL",
        lambda r: r.success and r.sql_result is not None and r.sql_result.row_count > 0,
        "SQL ranking — top N with filter",
    ),
    (
        "how many CSE students are there",
        "SQL",
        lambda r: r.success and len(r.response) > 0,
        "SQL count aggregate",
    ),
    (
        "tell me about roll number 2104920100002",
        "RAG",
        lambda r: r.success and len(r.response) > 50,
        "RAG student lookup by roll number",
    ),
    (
        "which students are struggling in programming subjects",
        "RAG",
        lambda r: r.success and len(r.response) > 50,
        "RAG semantic query",
    ),
    (
        "KCS503 results",
        "RAG",
        lambda r: r.success and len(r.response) > 20,
        "BM25 keyword search — subject code",
    ),
    (
        "analyze the CSE batch performance trend across semesters",
        "HYBRID",
        lambda r: r.success and len(r.response) > 100,
        "HYBRID analysis query",
    ),
]

_PASS_THRESHOLD = 70  # %


# ── Factory ────────────────────────────────────────────────────────────────────

def build_orchestrator() -> Orchestrator:
    llm = OllamaClient()
    router = QueryRouter(llm)
    sql_pipeline = SQLPipeline(llm)
    milvus = MilvusSearchClient(uri=settings.milvus_uri)
    rag_pipeline = RAGPipeline(llm, milvus)
    return Orchestrator(llm, router, sql_pipeline, rag_pipeline, milvus)


# ── Automated test runner ──────────────────────────────────────────────────────

async def run_automated() -> None:
    llm = OllamaClient()
    health = await llm.health_check()
    if health["status"] != "ok":
        print(f"{RED}✗ Ollama not running: {health.get('message')}{RESET}")
        return

    print(f"Ollama running. Models: {health['models']}")
    print(f"Running {len(AUTOMATED_TESTS)} orchestrator tests...\n")

    orchestrator = build_orchestrator()
    passed = 0
    failed = 0
    errors: list[tuple[str, str]] = []

    for query, expected_route_prefix, validate_fn, description in AUTOMATED_TESTS:
        print(f"{'=' * 60}")
        print(f"Test: {description}")
        print(f"Query: \"{query}\"")

        start = time.time()
        try:
            result = await orchestrator.process_query(query)
            elapsed = (time.time() - start) * 1000

            route_label = result.route_used or "?"
            print(f"Route: {route_label} (expected: {expected_route_prefix}) | "
                  f"Time: {elapsed:.0f}ms")

            if result.success:
                preview = result.response[:200].replace("\n", " ")
                print(f"Response: {preview}{'...' if len(result.response) > 200 else ''}")

                if result.sql_result and result.sql_result.success:
                    print(f"SQL rows: {result.sql_result.row_count}")
                if result.rag_result and result.rag_result.success:
                    print(f"RAG chunks: {result.rag_result.chunk_count}")
            else:
                print(f"Error: {result.error}")

            ok = validate_fn(result)
            if ok:
                passed += 1
                print(f"{GREEN}✓ PASSED{RESET}")
            else:
                failed += 1
                fail_reason = f"validate_fn returned False (success={result.success}, response_len={len(result.response)})"
                errors.append((query, fail_reason))
                print(f"{RED}✗ FAILED — {fail_reason}{RESET}")

        except Exception as exc:
            elapsed = (time.time() - start) * 1000
            failed += 1
            errors.append((query, str(exc)))
            print(f"Time: {elapsed:.0f}ms")
            print(f"{RED}✗ ERROR: {exc}{RESET}")

        print()

    total = passed + failed
    pct = (passed / total * 100) if total > 0 else 0.0

    print("=" * 60)
    print(f"Orchestrator: {passed}/{total} passed ({pct:.0f}%)")
    print("=" * 60)

    if errors:
        print(f"\n{RED}Failed cases:{RESET}")
        for q, err in errors:
            print(f"  • \"{q}\" — {err}")

    if pct >= _PASS_THRESHOLD:
        print(f"\n{GREEN}✓ System is working end-to-end! "
              f"({pct:.0f}% >= {_PASS_THRESHOLD}% threshold){RESET}")
    else:
        print(f"\n{RED}✗ Accuracy too low "
              f"({pct:.0f}% < {_PASS_THRESHOLD}% threshold){RESET}")


# ── Interactive mode ───────────────────────────────────────────────────────────

async def run_interactive() -> None:
    """Interactive chat mode — maintains conversation history across turns."""
    llm = OllamaClient()
    health = await llm.health_check()
    if health["status"] != "ok":
        print(f"{RED}✗ Ollama not running: {health.get('message')}{RESET}")
        return

    orchestrator = build_orchestrator()
    chat_history: list[dict] = []

    print(f"\n{BLUE}{'=' * 60}")
    print("KCCITM AI Assistant — Interactive Test Mode")
    print("Type your questions. Type 'quit' to exit.")
    print(f"{'=' * 60}{RESET}\n")
    print(f"Suggested queries:")
    print(f"  • top 5 students by SGPA in semester 1")
    print(f"  • tell me about Aakash Singh")
    print(f"  • KCS503 results")
    print(f"  • students struggling in programming")
    print(f"  • analyze CSE batch performance across semesters")
    print(f"  • what about semester 3  (follow-up)\n")

    while True:
        try:
            query = input(f"{CYAN}You: {RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not query or query.lower() in ("quit", "exit", "q"):
            break

        start = time.time()
        result = await orchestrator.process_query(query, chat_history)
        elapsed = (time.time() - start) * 1000

        if result.success:
            print(f"\n{YELLOW}[{result.route_used} | {elapsed:.0f}ms]{RESET}")
            print(f"{WHITE}{result.response}{RESET}\n")
            chat_history.append({"role": "user", "content": query})
            chat_history.append({"role": "assistant", "content": result.response})
        else:
            print(f"\n{RED}Error: {result.error}{RESET}\n")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--interactive" in sys.argv:
        asyncio.run(run_interactive())
    else:
        asyncio.run(run_automated())

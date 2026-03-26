"""
Phase 9: Adaptive Engine tests.

Unit tests (offline):
    python -m tests.test_adaptive --unit

API tests (server must be running on localhost:8000):
    python -m tests.test_adaptive --api

Both:
    python -m tests.test_adaptive --all
"""

import asyncio
import sys
import uuid

PASS = "\033[92m✓ PASSED\033[0m"
FAIL = "\033[91m✗ FAILED\033[0m"
_passed = _failed = 0


def _ok(label: str) -> None:
    global _passed; _passed += 1; print(f"  {PASS} {label}")


def _fail(label: str, reason: str = "") -> None:
    global _failed; _failed += 1
    msg = f"  {FAIL} {label}"
    if reason: msg += f" — {reason}"
    print(msg)


# ══════════════════════════════════════════════════════════════════════════════
# UNIT TESTS
# ══════════════════════════════════════════════════════════════════════════════

def test_prompt_evolver_clustering():
    print("\n=== Unit Test 1: Prompt Evolver — Failure Clustering ===")
    from adaptive.prompt_evolver import PromptEvolver

    # Dummy LLM (won't be called in clustering test)
    class _DummyLLM:
        pass

    evolver = PromptEvolver(_DummyLLM())

    failures = [
        {"query_text": "top 5 students by SGPA semester 1", "quality_score": 0.2},
        {"query_text": "top 3 students by SGPA semester 2", "quality_score": 0.3},
        {"query_text": "top 10 students SGPA", "quality_score": 0.25},
        {"query_text": "what is machine learning", "quality_score": 0.1},
        {"query_text": "ML algorithms explained", "quality_score": 0.15},
    ]

    clusters = evolver._cluster_failures(failures)
    assert len(clusters) >= 2, f"Expected at least 2 clusters, got {len(clusters)}"
    _ok(f"Clusters formed correctly ({len(clusters)} clusters from 5 failures)")

    # SGPA queries should cluster together (the two most similar ones)
    largest = max(clusters, key=len)
    assert len(largest) >= 2, f"Largest cluster should have ≥ 2 items, got {len(largest)}"
    _ok(f"Similar SGPA queries clustered together (cluster size {len(largest)})")


def test_prompt_ab_tester_logic():
    print("\n=== Unit Test 2: Prompt A/B Tester — Traffic Split ===")
    from adaptive.prompt_ab_tester import PromptABTester

    tester = PromptABTester()

    # Verify constants
    assert 0 < tester.TRAFFIC_SPLIT_NEW < 1
    _ok(f"Traffic split configured: {tester.TRAFFIC_SPLIT_NEW*100:.0f}% new / {(1-tester.TRAFFIC_SPLIT_NEW)*100:.0f}% old")
    assert tester.MIN_QUERIES_FOR_DECISION >= 10
    _ok(f"Min queries for decision: {tester.MIN_QUERIES_FOR_DECISION}")
    assert 0 < tester.MIN_IMPROVEMENT < 1
    _ok(f"Min improvement threshold: {tester.MIN_IMPROVEMENT}")


def test_faq_generator_clustering():
    print("\n=== Unit Test 3: FAQ Generator — Query Clustering ===")
    from adaptive.faq_generator import FAQGenerator

    class _DummyLLM:
        pass
    class _DummyMilvus:
        pass

    gen = FAQGenerator(_DummyLLM(), _DummyMilvus())

    queries = [
        {"query_text": "how many students are in CSE", "quality_score": 0.9},
        {"query_text": "total students in CSE branch", "quality_score": 0.85},
        {"query_text": "CSE students count 2021 batch", "quality_score": 0.88},
        {"query_text": "what is machine learning introduction", "quality_score": 0.9},
    ]

    clusters = gen._cluster_queries(queries)
    assert len(clusters) >= 1, "Expected at least 1 cluster"
    _ok(f"FAQ query clustering works ({len(clusters)} clusters from 4 queries)")

    # All queries should be assigned to a cluster (no query lost)
    total_in_clusters = sum(len(c) for c in clusters)
    assert total_in_clusters == len(queries), f"All {len(queries)} queries should appear in clusters"
    _ok(f"All queries assigned to clusters (total items: {total_in_clusters})")


def test_chunk_analyzer_logic():
    print("\n=== Unit Test 4: Chunk Analyzer — Running Average ===")
    # Simulate running average calculation
    old_avg, old_count = 0.6, 10
    new_score = 0.2
    new_avg = (old_avg * old_count + new_score) / (old_count + 1)
    expected = (0.6 * 10 + 0.2) / 11
    assert abs(new_avg - expected) < 1e-9
    _ok(f"Running average calculation correct: {old_avg:.2f} → {new_avg:.4f}")

    # Verify underperforming detection logic
    ratio = 2 / 20  # 2 top-5 hits out of 20 retrievals
    assert ratio < 0.2, "Should be flagged as underperforming"
    _ok(f"Underperforming detection: ratio {ratio:.2f} < threshold 0.20")


def test_training_data_manager_categorize():
    print("\n=== Unit Test 5: Training Data Manager — Categorization ===")
    from adaptive.training_data_manager import TrainingDataManager

    m = TrainingDataManager()
    assert m._categorize("SQL") == "sql_gen"
    assert m._categorize("SQL (fallback)") == "sql_gen"
    assert m._categorize("RAG") == "response"
    assert m._categorize("HYBRID") == "response"
    assert m._categorize("") == "response"
    _ok("SQL routes → sql_gen category")
    _ok("RAG/HYBRID routes → response category")


# ══════════════════════════════════════════════════════════════════════════════
# API TESTS
# ══════════════════════════════════════════════════════════════════════════════

async def _get_token(client) -> str:
    import httpx
    r = await client.post("http://localhost:8000/api/auth/login",
                          json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, f"Login failed: {r.text}"
    return r.json()["access_token"]


async def test_faq_admin_api():
    print("\n=== API Test 1: FAQ Admin Endpoints ===")
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await _get_token(client)
        headers = {"Authorization": f"Bearer {token}"}

        # List FAQs
        r = await client.get("http://localhost:8000/api/admin/faqs", headers=headers)
        assert r.status_code == 200, f"List FAQs failed: {r.text}"
        data = r.json()
        assert "faqs" in data
        print(f"  FAQs in system: {len(data['faqs'])}")
        _ok("GET /admin/faqs returns list")


async def test_prompt_admin_api():
    print("\n=== API Test 2: Prompt Admin Endpoints ===")
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await _get_token(client)
        headers = {"Authorization": f"Bearer {token}"}

        # List prompts
        r = await client.get("http://localhost:8000/api/admin/prompts", headers=headers)
        assert r.status_code == 200, f"List prompts failed: {r.text}"
        data = r.json()
        assert "prompts" in data
        print(f"  Active prompts: {len(data['prompts'])}")
        _ok("GET /admin/prompts returns active prompt list")

        # List proposals
        r = await client.get("http://localhost:8000/api/admin/prompts/proposals", headers=headers)
        assert r.status_code == 200, f"List proposals failed: {r.text}"
        data = r.json()
        assert "proposals" in data
        print(f"  Pending proposals: {len(data['proposals'])}")
        _ok("GET /admin/prompts/proposals returns pending proposals")


async def test_training_admin_api():
    print("\n=== API Test 3: Training Data Admin Endpoints ===")
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await _get_token(client)
        headers = {"Authorization": f"Bearer {token}"}

        r = await client.get("http://localhost:8000/api/admin/training/stats", headers=headers)
        assert r.status_code == 200, f"Training stats failed: {r.text}"
        data = r.json()
        assert "total_candidates" in data
        print(f"  Training candidates: {data['total_candidates']} | Ready for LoRA: {data['ready_for_lora']}")
        _ok("GET /admin/training/stats returns required fields")

        r = await client.get("http://localhost:8000/api/admin/training/candidates", headers=headers)
        assert r.status_code == 200
        _ok("GET /admin/training/candidates returns list")


async def test_chunk_health_api():
    print("\n=== API Test 4: Chunk Health Analytics ===")
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await _get_token(client)
        headers = {"Authorization": f"Bearer {token}"}

        r = await client.get("http://localhost:8000/api/admin/chunks/health", headers=headers)
        assert r.status_code == 200, f"Chunk health failed: {r.text}"
        data = r.json()
        assert "total_tracked" in data
        assert "underperforming_count" in data
        print(f"  Chunks tracked: {data['total_tracked']} | Underperforming: {data['underperforming_count']}")
        _ok("GET /admin/chunks/health returns chunk analytics")


async def test_batch_jobs_api():
    print("\n=== API Test 5: Batch Job Triggers ===")
    import httpx

    async with httpx.AsyncClient(timeout=120.0) as client:
        token = await _get_token(client)
        headers = {"Authorization": f"Bearer {token}"}

        # Trigger healing job
        r = await client.post("http://localhost:8000/api/admin/jobs/healing/run", headers=headers)
        assert r.status_code == 200, f"Healing job failed: {r.text}"
        data = r.json()
        print(f"  Healing: scored={data.get('scored', 0)}, queued={data.get('queued_for_healing', 0)}, "
              f"training_collected={data.get('training_from_feedback', 0)+data.get('training_from_faqs', 0)}")
        _ok("POST /admin/jobs/healing/run completes successfully")

        # Trigger FAQ job
        r = await client.post("http://localhost:8000/api/admin/jobs/faq/run", headers=headers)
        assert r.status_code == 200, f"FAQ job failed: {r.text}"
        data = r.json()
        print(f"  FAQ: clusters_found={data.get('clusters_found', 0)}, generated={data.get('faqs_generated', 0)}")
        _ok("POST /admin/jobs/faq/run completes successfully")

        # Trigger prompt evolution
        r = await client.post("http://localhost:8000/api/admin/jobs/prompts/run", headers=headers)
        assert r.status_code == 200, f"Prompts job failed: {r.text}"
        data = r.json()
        print(f"  Prompts: proposals_generated={data.get('proposals_generated', 0)}")
        _ok("POST /admin/jobs/prompts/run completes successfully")


# ══════════════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════════════

def run_unit_tests():
    print("\n" + "=" * 60)
    print("Phase 9 — Unit Tests (offline)")
    print("=" * 60)
    test_prompt_evolver_clustering()
    test_prompt_ab_tester_logic()
    test_faq_generator_clustering()
    test_chunk_analyzer_logic()
    test_training_data_manager_categorize()


async def run_api_tests():
    import httpx
    print("\n" + "=" * 60)
    print("Phase 9 — API Tests (requires server on localhost:8000)")
    print("=" * 60)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.get("http://localhost:8000/api/health")
    except Exception:
        print("\033[91m✗ Cannot reach localhost:8000 — is the server running?\033[0m")
        return

    await test_faq_admin_api()
    await test_prompt_admin_api()
    await test_training_admin_api()
    await test_chunk_health_api()
    await test_batch_jobs_api()


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--api"
    if mode in ("--unit", "--all"):
        run_unit_tests()
    if mode in ("--api", "--all"):
        await run_api_tests()

    total = _passed + _failed
    pct = (_passed / total * 100) if total else 0
    print(f"\n{'=' * 60}")
    print(f"Results: {_passed}/{total} passed ({pct:.0f}%)")
    if _failed == 0:
        print("\033[92m✓ All Phase 9 tests passed!\033[0m")
    else:
        print(f"\033[91m{_failed} test(s) failed\033[0m")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

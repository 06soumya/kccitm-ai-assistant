"""
Phase 8: Feedback System + Quality Scoring + Failure Classification tests.

Two test modes:
  1. Unit tests (offline — no server required):
       python -m tests.test_feedback --unit
  2. API tests (server must be running on localhost:8000):
       python -m tests.test_feedback

Run both:
    python -m tests.test_feedback --all
"""

import asyncio
import json
import sys
import uuid

PASS = "\033[92m✓ PASSED\033[0m"
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
    msg = f"  {FAIL} {label}"
    if reason:
        msg += f" — {reason}"
    print(msg)


# ══════════════════════════════════════════════════════════════════════════════
# UNIT TESTS (no server, no DB)
# ══════════════════════════════════════════════════════════════════════════════

def test_quality_scorer_unit():
    print("\n=== Unit Test 1: Quality Scorer ===")
    from adaptive.quality_scorer import compute_quality_score

    # Perfect response: high rating, ideal length, high confidence, no negative signals
    score = compute_quality_score(
        response_text="A" * 300,
        rating=5,
        confidence_score=0.95,
        implicit_signals=[],
    )
    assert score >= 0.80, f"Expected high score, got {score}"
    _ok(f"High-quality response scores ≥ 0.80 (got {score:.3f})")

    # Terrible response: 1-star, very short, low confidence
    score = compute_quality_score(
        response_text="No.",
        rating=1,
        confidence_score=0.2,
        implicit_signals=["rephrase", "follow_up"],
    )
    assert score <= 0.30, f"Expected low score, got {score}"
    _ok(f"Low-quality response scores ≤ 0.30 (got {score:.3f})")

    # Medium response: no explicit rating
    score = compute_quality_score(
        response_text="A" * 200,
        rating=None,
        confidence_score=0.65,
        implicit_signals=[],
    )
    assert 0.3 <= score <= 0.9, f"Expected medium score, got {score}"
    _ok(f"No-rating response scores in reasonable range (got {score:.3f})")

    # Rephrase signal lowers score
    s_clean = compute_quality_score("A" * 200, rating=4, confidence_score=0.8, implicit_signals=[])
    s_rephrase = compute_quality_score("A" * 200, rating=4, confidence_score=0.8, implicit_signals=["rephrase"])
    assert s_rephrase < s_clean, "Rephrase signal should lower score"
    _ok("Rephrase implicit signal lowers quality score")


def test_failure_classifier_unit():
    print("\n=== Unit Test 2: Failure Classifier ===")
    from adaptive.failure_classifier import classify_failure

    # No failure for good score
    cat = classify_failure(
        query="top 5 students",
        response_text="Here are the top 5 students: ...",
        route_used="SQL",
        sql_row_count=5,
        sql_error=None,
        chunk_count=0,
        quality_score=0.75,
    )
    assert cat is None, f"Expected None, got {cat}"
    _ok("Good response returns no failure category")

    # SQL error
    cat = classify_failure(
        query="top 5 students",
        response_text="Error occurred",
        route_used="SQL",
        sql_row_count=0,
        sql_error="syntax error near SELECT",
        chunk_count=0,
        quality_score=0.20,
    )
    assert cat == "sql_error", f"Expected sql_error, got {cat}"
    _ok("SQL error correctly classified")

    # No data
    cat = classify_failure(
        query="marks for roll 999",
        response_text="No results found",
        route_used="SQL",
        sql_row_count=0,
        sql_error=None,
        chunk_count=0,
        quality_score=0.20,
    )
    assert cat == "no_data", f"Expected no_data, got {cat}"
    _ok("Empty SQL results classified as no_data")

    # Incomplete
    cat = classify_failure(
        query="explain the entire curriculum structure for CSE students",
        response_text="Yes.",
        route_used="RAG",
        sql_row_count=None,
        sql_error=None,
        chunk_count=3,
        quality_score=0.25,
    )
    assert cat == "incomplete", f"Expected incomplete, got {cat}"
    _ok("Very short response to long query classified as incomplete")

    # Hallucination heuristic (chunk_count > 0 so no_data doesn't fire first)
    cat = classify_failure(
        query="what is the pass percentage",
        response_text=(
            "I believe it probably might be around 70 percent. "
            "It likely could be higher, and generally speaking it seems like "
            "it appears to be a good result."
        ),
        route_used="RAG",
        sql_row_count=None,
        sql_error=None,
        chunk_count=3,
        quality_score=0.30,
    )
    assert cat == "hallucination", f"Expected hallucination, got {cat}"
    _ok("Hedging language classified as hallucination")


def test_implicit_signal_unit():
    print("\n=== Unit Test 3: Implicit Signal Detection ===")
    from adaptive.feedback_collector import detect_implicit_signals

    # Rephrase within 60s
    signals = detect_implicit_signals(
        current_query="top students by sgpa sem 1",
        previous_query="top 5 students by sgpa semester 1",
        time_gap_seconds=45,
        session_turn_count=2,
    )
    assert "rephrase" in signals, f"Expected rephrase, got {signals}"
    _ok("Rephrase detected (similar query, < 120s)")

    # Follow-up short question
    signals = detect_implicit_signals(
        current_query="what about sem 2?",
        previous_query="show me topper of semester 1",
        time_gap_seconds=30,
        session_turn_count=3,
    )
    assert "follow_up" in signals, f"Expected follow_up, got {signals}"
    _ok("Follow-up detected (short question, < 90s)")

    # Long session
    signals = detect_implicit_signals(
        current_query="any more details?",
        previous_query="give me CSE results",
        time_gap_seconds=200,
        session_turn_count=8,
    )
    assert "long_session" in signals, f"Expected long_session, got {signals}"
    _ok("Long session (≥ 6 turns) positive signal detected")

    # No signals for normal interaction
    signals = detect_implicit_signals(
        current_query="what is the average SGPA of ECE batch 2022?",
        previous_query="show top 3 students",
        time_gap_seconds=300,
        session_turn_count=2,
    )
    assert "rephrase" not in signals
    assert "follow_up" not in signals
    _ok("No false-positive signals for normal interaction")


def test_edit_distance_unit():
    print("\n=== Unit Test 4: Edit Distance ===")
    from adaptive.feedback_collector import _edit_distance_ratio

    assert _edit_distance_ratio("hello", "hello") == 0.0
    _ok("Identical strings → distance 0.0")

    ratio = _edit_distance_ratio("top 5 students", "top 5 student")
    assert ratio < 0.15, f"Very similar strings should be close: {ratio}"
    _ok(f"Very similar strings → low distance ({ratio:.3f})")

    ratio = _edit_distance_ratio("abc", "xyz")
    assert ratio >= 0.8, f"Completely different strings should be far: {ratio}"
    _ok(f"Completely different strings → high distance ({ratio:.3f})")


# ══════════════════════════════════════════════════════════════════════════════
# API TESTS (server required)
# ══════════════════════════════════════════════════════════════════════════════

async def _get_token(client) -> str:
    import httpx
    r = await client.post("http://localhost:8000/api/auth/login",
                          json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, f"Login failed: {r.text}"
    return r.json()["access_token"]


async def test_feedback_api():
    print("\n=== API Test 1: Submit Explicit Feedback ===")
    import httpx

    async with httpx.AsyncClient(timeout=120.0) as client:
        token = await _get_token(client)
        headers = {"Authorization": f"Bearer {token}"}

        # Submit feedback
        payload = {
            "message_id": str(uuid.uuid4()),
            "session_id": str(uuid.uuid4()),
            "rating": 5,
            "feedback_text": "Great answer!",
            "query_text": "top 3 students by SGPA",
            "response_text": "Here are the top 3 students: " + "A" * 100,
            "route_used": "SQL",
            "confidence_score": 0.9,
        }
        r = await client.post("http://localhost:8000/api/feedback",
                              headers=headers, json=payload)
        assert r.status_code == 200, f"Feedback failed: {r.text}"
        data = r.json()

        assert "feedback_id" in data and data["feedback_id"]
        _ok("Submit feedback returns feedback_id")

        assert "quality_score" in data
        assert 0.0 <= data["quality_score"] <= 1.0
        print(f"  Quality score: {data['quality_score']}")
        _ok(f"Quality score in [0,1] range (got {data['quality_score']:.3f})")

        # Submit low-rating feedback
        low_payload = {
            "message_id": str(uuid.uuid4()),
            "session_id": str(uuid.uuid4()),
            "rating": 1,
            "query_text": "marks for roll 999999",
            "response_text": "No.",
            "route_used": "SQL",
            "confidence_score": 0.2,
        }
        r = await client.post("http://localhost:8000/api/feedback",
                              headers=headers, json=low_payload)
        assert r.status_code == 200
        data = r.json()
        print(f"  Low-quality score: {data['quality_score']} | category: {data.get('failure_category')}")
        _ok("Low-rating feedback processed without error")


async def test_feedback_stats_api():
    print("\n=== API Test 2: Feedback Stats ===")
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await _get_token(client)
        headers = {"Authorization": f"Bearer {token}"}

        r = await client.get("http://localhost:8000/api/feedback/stats", headers=headers)
        assert r.status_code == 200, f"Stats failed: {r.text}"
        data = r.json()

        required = {"total_feedback", "avg_rating", "avg_quality_score",
                    "positive_feedback", "negative_feedback", "healed_responses"}
        assert required <= data.keys(), f"Missing keys: {required - data.keys()}"
        print(f"  Total: {data['total_feedback']} | Avg rating: {data['avg_rating']} "
              f"| Avg quality: {data['avg_quality_score']}")
        _ok("Feedback stats returns all required fields")


async def test_dashboard_api():
    print("\n=== API Test 3: Dashboard Endpoints ===")
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await _get_token(client)
        headers = {"Authorization": f"Bearer {token}"}

        # Quality dashboard
        r = await client.get("http://localhost:8000/api/admin/dashboard/quality",
                              headers=headers)
        assert r.status_code == 200, f"Quality dashboard failed: {r.text}"
        data = r.json()
        assert "score_distribution" in data
        assert "daily_avg" in data
        print(f"  Quality distribution: {data['score_distribution']}")
        _ok("Quality dashboard returns score_distribution and daily_avg")

        # Failure dashboard
        r = await client.get("http://localhost:8000/api/admin/dashboard/failures",
                              headers=headers)
        assert r.status_code == 200, f"Failure dashboard failed: {r.text}"
        _ok("Failure dashboard returns without error")

        # Healing queue
        r = await client.get("http://localhost:8000/api/admin/dashboard/healing",
                              headers=headers)
        assert r.status_code == 200, f"Healing queue failed: {r.text}"
        data = r.json()
        assert "items" in data
        print(f"  Healing queue pending: {data['total']}")
        _ok("Healing queue endpoint returns items list")

        # Chunk analytics
        r = await client.get("http://localhost:8000/api/admin/dashboard/chunks",
                              headers=headers)
        assert r.status_code == 200, f"Chunk analytics failed: {r.text}"
        _ok("Chunk analytics endpoint returns without error")


# ══════════════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════════════

def run_unit_tests():
    print("\n" + "=" * 60)
    print("Phase 8 — Unit Tests (offline)")
    print("=" * 60)
    test_quality_scorer_unit()
    test_failure_classifier_unit()
    test_implicit_signal_unit()
    test_edit_distance_unit()


async def run_api_tests():
    import httpx
    print("\n" + "=" * 60)
    print("Phase 8 — API Tests (requires server on localhost:8000)")
    print("=" * 60)

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.get("http://localhost:8000/api/health")
    except Exception:
        print("\033[91m✗ Cannot reach localhost:8000 — is the server running?\033[0m")
        print("  Start with: uvicorn main:app --port 8000")
        return

    await test_feedback_api()
    await test_feedback_stats_api()
    await test_dashboard_api()


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--api"

    if mode in ("--unit", "--all"):
        run_unit_tests()

    if mode in ("--api", "--all"):
        await run_api_tests()

    total = _passed + _failed
    pct = (_passed / total * 100) if total > 0 else 0
    print(f"\n{'=' * 60}")
    print(f"Results: {_passed}/{total} passed ({pct:.0f}%)")
    if _failed == 0:
        print("\033[92m✓ All Phase 8 tests passed!\033[0m")
    else:
        print(f"\033[91m{_failed} test(s) failed\033[0m")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

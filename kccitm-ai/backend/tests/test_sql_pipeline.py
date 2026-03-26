"""
SQL Pipeline end-to-end test suite.

Tests against live MySQL + Ollama (llama3.2:latest).
Each test calls the LLM to generate SQL, then executes against the DB.

Run:
    cd backend
    python -m tests.test_sql_pipeline

Requirements:
    - MySQL running with kccitm database populated (run ingestion/etl.py first)
    - Ollama running with llama3.2:latest
"""

import asyncio

from core.llm_client import OllamaClient
from core.router import QueryRouter
from core.sql_pipeline import SQLPipeline, SQLResult

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

# ── Test cases ─────────────────────────────────────────────────────────────────
# Format: (query, checks: dict, description)
# checks keys:
#   row_count_gte   — result.row_count >= N
#   row_count_lte   — result.row_count <= N
#   row_count_eq    — result.row_count == N
#   has_column      — column key present in first row
#   sql_contains    — substring in generated SQL (case-insensitive)
#   success         — bool (default True)

TEST_CASES = [
    (
        "top 5 students by SGPA in semester 4",
        {
            "success": True,
            "row_count_gte": 1,
            "row_count_lte": 5,
            "sql_contains": "semester",
        },
        "Top-N ranking query",
    ),
    (
        "how many students are in the database",
        {
            "success": True,
            "row_count_gte": 1,
            "sql_contains": "COUNT",
        },
        "COUNT aggregate",
    ),
    (
        "list all students with SGPA above 9 in semester 1",
        {
            "success": True,
            "row_count_gte": 1,
            "sql_contains": "sgpa",
        },
        "Threshold filter query",
    ),
    (
        "what is the average SGPA of CSE students in semester 3",
        {
            "success": True,
            "row_count_gte": 1,
            "sql_contains": "AVG",
        },
        "AVG aggregate with branch filter",
    ),
    (
        "how many students failed in semester 2",
        {
            "success": True,
            "row_count_gte": 1,
            "sql_contains": "result_status",
        },
        "FAIL/CP filter query",
    ),
    (
        "show marks for roll number 2104920100002",
        {
            "success": True,
            "row_count_gte": 1,
            "sql_contains": "2104920100002",
        },
        "Specific roll number lookup",
    ),
    (
        "which students have back papers in semester 5",
        {
            "success": True,
            "row_count_gte": 1,
            "sql_contains": "back_paper",
        },
        "Back paper filter",
    ),
    (
        "count students with grade A in semester 3",
        {
            "success": True,
            "row_count_gte": 1,
            "sql_contains": "grade",
        },
        "Grade filter count",
    ),
    (
        "top 3 students by total marks in semester 6 in CSE branch",
        {
            "success": True,
            "row_count_gte": 1,
            "row_count_lte": 3,
            "sql_contains": "total_marks",
        },
        "Multi-filter top-N with branch",
    ),
    (
        "what subjects does semester 1 have",
        {
            "success": True,
            "row_count_gte": 1,
            "sql_contains": "subject_name",
        },
        "Subject listing query",
    ),
]

# Pass threshold
_PASS_THRESHOLD = 70  # %


# ── Helpers ────────────────────────────────────────────────────────────────────

def _check_result(result: SQLResult, checks: dict) -> list[str]:
    """Return list of failure messages (empty = all passed)."""
    failures = []

    expected_success = checks.get("success", True)
    if result.success != expected_success:
        failures.append(
            f"success: expected {expected_success}, got {result.success} "
            f"(error: {result.error})"
        )
        return failures  # No point checking further if success mismatch

    if "row_count_gte" in checks:
        if result.row_count < checks["row_count_gte"]:
            failures.append(
                f"row_count: expected >= {checks['row_count_gte']}, got {result.row_count}"
            )

    if "row_count_lte" in checks:
        if result.row_count > checks["row_count_lte"]:
            failures.append(
                f"row_count: expected <= {checks['row_count_lte']}, got {result.row_count}"
            )

    if "row_count_eq" in checks:
        if result.row_count != checks["row_count_eq"]:
            failures.append(
                f"row_count: expected == {checks['row_count_eq']}, got {result.row_count}"
            )

    if "has_column" in checks and result.rows:
        col = checks["has_column"]
        if col not in result.rows[0]:
            failures.append(f"column '{col}' not in result row keys: {list(result.rows[0].keys())}")

    if "sql_contains" in checks:
        needle = checks["sql_contains"].upper()
        if needle not in result.sql.upper():
            failures.append(f"SQL missing '{checks['sql_contains']}': {result.sql[:100]}")

    return failures


# ── Runner ─────────────────────────────────────────────────────────────────────

async def run_tests() -> None:
    llm = OllamaClient()
    router = QueryRouter(llm)
    pipeline = SQLPipeline(llm)

    # Verify dependencies
    health = await llm.health_check()
    if health["status"] != "ok":
        print(f"{RED}✗ Ollama not running: {health.get('message')}{RESET}")
        return

    print(f"Ollama running. Models: {health['models']}")
    print(f"Running {len(TEST_CASES)} SQL pipeline tests...\n")
    print("=" * 70)

    passed = 0
    failed = 0
    errors: list[tuple[str, list[str]]] = []

    for query, checks, description in TEST_CASES:
        try:
            # Route first to get context
            route_result = await router.route(query)
            result = await pipeline.run(query, route_result)

            failures = _check_result(result, checks)

            if not failures:
                passed += 1
                print(f"{GREEN}✓ {description}{RESET}")
                print(f"  Query:    \"{query}\"")
                print(f"  SQL:      {result.sql[:80]}{'...' if len(result.sql) > 80 else ''}")
                print(f"  Rows:     {result.row_count}  |  Time: {result.execution_time_ms:.1f}ms")
            else:
                failed += 1
                errors.append((query, failures))
                print(f"{RED}✗ {description}{RESET}")
                print(f"  Query:    \"{query}\"")
                print(f"  SQL:      {result.sql[:80]}{'...' if len(result.sql) > 80 else ''}")
                for f in failures:
                    print(f"  FAIL:     {f}")

        except Exception as exc:
            failed += 1
            errors.append((query, [str(exc)]))
            print(f"{RED}✗ {description} — ERROR: {exc}{RESET}")

        print()

    # ── Summary ───────────────────────────────────────────────────────────────
    total = passed + failed
    pct = (passed / total * 100) if total > 0 else 0.0

    print("=" * 70)
    print(f"SQL Pipeline Test Results: {passed}/{total} passed ({pct:.0f}%)")
    print("=" * 70)

    if errors:
        print(f"\n{RED}Failed cases:{RESET}")
        for q, failures in errors:
            print(f"  • \"{q}\"")
            for f in failures:
                print(f"    - {f}")

    if pct >= _PASS_THRESHOLD:
        print(
            f"\n{GREEN}✓ SQL pipeline accuracy is acceptable "
            f"({pct:.0f}% >= {_PASS_THRESHOLD}% threshold){RESET}"
        )
    else:
        print(
            f"\n{RED}✗ SQL pipeline accuracy too low "
            f"({pct:.0f}% < {_PASS_THRESHOLD}% threshold). "
            f"Check LLM prompt or DB connection.{RESET}"
        )


if __name__ == "__main__":
    asyncio.run(run_tests())

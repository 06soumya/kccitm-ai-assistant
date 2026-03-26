"""
Router classification test suite.

Tests against the actual Ollama LLM — requires Ollama running with qwen3:8b.
Each test case calls the LLM, so this takes 2-5 minutes to complete.

Run:
    cd backend
    python -m tests.test_router
"""

import asyncio

from core.llm_client import OllamaClient
from core.router import QueryRouter

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

# ── Test cases ────────────────────────────────────────────────────────────────
# Format: (query, expected_route, expected_filters_subset, description)
# expected_filters_subset: only keys listed here must match;
#                          result may have ADDITIONAL filters — that's fine.

TEST_CASES = [
    # === SQL ROUTE ===
    (
        "top 5 students by SGPA in semester 4",
        "SQL", {"semester": 4},
        "Ranking query with semester → SQL",
    ),
    (
        "what is the average SGPA of CSE students",
        "SQL", {"branch": "COMPUTER SCIENCE AND ENGINEERING"},
        "Aggregate with branch abbreviation → SQL",
    ),
    (
        "how many students failed in semester 3",
        "SQL", {"semester": 3},
        "Count query → SQL",
    ),
    (
        "what was Aakash Singh's SGPA in semester 1",
        "SQL", {"semester": 1},
        "Specific named lookup → SQL",
    ),
    (
        "compare pass rates between semester 1 and semester 6",
        "SQL", {},
        "Comparison query → SQL",
    ),
    (
        "count students with grade C in KCS503",
        "SQL", {"subject_code": "KCS503"},
        "Subject-specific count → SQL",
    ),
    (
        "list all students with SGPA above 9",
        "SQL", {},
        "Threshold/list query → SQL",
    ),

    # === RAG ROUTE ===
    (
        "tell me about roll number 2104920100002",
        "RAG", {"roll_no": "2104920100002"},
        "Descriptive roll number query → RAG",
    ),
    (
        "which students are struggling in programming subjects",
        "RAG", {},
        "Semantic/qualitative → RAG",
    ),
    (
        "describe the overall performance of the 2021 CSE batch",
        "RAG", {"branch": "COMPUTER SCIENCE AND ENGINEERING"},
        "Descriptive analysis with branch → RAG",
    ),
    (
        "students who performed well in practicals",
        "RAG", {},
        "Qualitative performance → RAG",
    ),
    (
        "who are the weak students in semester 5",
        "RAG", {"semester": 5},
        "Qualitative with semester filter → RAG",
    ),

    # === HYBRID ROUTE ===
    (
        "why did the average SGPA drop in semester 6 compared to semester 4",
        "HYBRID", {},
        "Needs data + causal analysis → HYBRID",
    ),
    (
        "which CSE students improved the most from semester 1 to semester 4 and why",
        "HYBRID", {"branch": "COMPUTER SCIENCE AND ENGINEERING"},
        "Trend + explanation → HYBRID",
    ),

    # === EDGE CASES ===
    (
        "KCS503",
        "RAG", {"subject_code": "KCS503"},
        "Bare subject code → RAG",
    ),
    (
        "hello",
        "RAG", {},
        "Greeting falls back to RAG",
    ),
    (
        "what about semester 3",
        "SQL", {"semester": 3},
        "Ambiguous follow-up → best guess with semester filter",
    ),

    # === BRANCH ABBREVIATION MAPPING ===
    (
        "top CSE students",
        "SQL", {"branch": "COMPUTER SCIENCE AND ENGINEERING"},
        "CSE abbreviation expanded correctly",
    ),
    (
        "ECE semester 4 results",
        "SQL", {"branch": "ELECTRONICS AND COMMUNICATION ENGINEERING", "semester": 4},
        "ECE abbreviation + semester filter",
    ),

    # === ADDITIONAL ROBUSTNESS ===
    (
        "average marks of students in B.TECH CSE semester 2",
        "SQL", {"branch": "COMPUTER SCIENCE AND ENGINEERING", "semester": 2},
        "B.TECH course + branch + semester → SQL",
    ),
]

# Pass threshold — LLM classification is probabilistic
_PASS_THRESHOLD = 75  # %


async def run_tests() -> None:
    llm = OllamaClient()
    router = QueryRouter(llm)

    # Verify Ollama is reachable
    health = await llm.health_check()
    if health["status"] != "ok":
        print(f"{RED}✗ Ollama not running: {health.get('message')}{RESET}")
        return

    print(f"Ollama running. Models: {health['models']}")
    print(f"Running {len(TEST_CASES)} router classification tests...\n")
    print("=" * 70)

    passed = 0
    failed = 0
    errors: list[tuple[str, str]] = []

    for query, expected_route, expected_filters, description in TEST_CASES:
        try:
            result = await router.route(query)

            route_ok = result.route == expected_route
            filters_ok = all(
                result.filters.get(k) == v
                for k, v in expected_filters.items()
                if v is not None
            )

            if route_ok and filters_ok:
                passed += 1
                print(f"{GREEN}✓ {description}{RESET}")
                print(f"  Query:   \"{query}\"")
                print(f"  Route:   {result.route}  |  Filters: {result.filters}")
                print(f"  Intent:  {result.intent}")
            else:
                failed += 1
                err_parts: list[str] = []
                if not route_ok:
                    err_parts.append(
                        f"route: expected {expected_route}, got {result.route}"
                    )
                if not filters_ok:
                    expected_str = {
                        k: v for k, v in expected_filters.items() if v is not None
                    }
                    err_parts.append(
                        f"filters: expected {expected_str}, got {result.filters}"
                    )
                error_msg = " | ".join(err_parts)
                errors.append((query, error_msg))

                print(f"{RED}✗ {description}{RESET}")
                print(f"  Query:   \"{query}\"")
                print(f"  {error_msg}")
                print(
                    f"  Full:    route={result.route}, "
                    f"filters={result.filters}, intent={result.intent}"
                )

        except Exception as exc:
            failed += 1
            errors.append((query, str(exc)))
            print(f"{RED}✗ {description} — ERROR: {exc}{RESET}")

        print()

    # ── Summary ───────────────────────────────────────────────────────────────
    total = passed + failed
    pct = (passed / total * 100) if total > 0 else 0.0

    print("=" * 70)
    print(f"Router Test Results: {passed}/{total} passed ({pct:.0f}%)")
    print("=" * 70)

    if errors:
        print(f"\n{RED}Failed cases:{RESET}")
        for q, err in errors:
            print(f"  • \"{q}\" — {err}")

    if pct >= _PASS_THRESHOLD:
        print(
            f"\n{GREEN}✓ Router accuracy is acceptable "
            f"({pct:.0f}% >= {_PASS_THRESHOLD}% threshold){RESET}"
        )
    else:
        print(
            f"\n{RED}✗ Router accuracy too low "
            f"({pct:.0f}% < {_PASS_THRESHOLD}% threshold). "
            f"Prompt needs tuning.{RESET}"
        )


if __name__ == "__main__":
    asyncio.run(run_tests())

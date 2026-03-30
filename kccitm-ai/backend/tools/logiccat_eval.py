"""
Evaluate our text-to-SQL system against LogicCat-style benchmarks.

Tests the pipeline end-to-end: NL question -> SQL generation -> execution -> verification.

Usage:
    python -m tools.logiccat_eval                           # Run built-in KCCITM test suite
    python -m tools.logiccat_eval --dataset path/to/data.json  # Run against external dataset
    python -m tools.logiccat_eval --verbose                 # Show generated SQL
"""

import argparse
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Built-in test suite for KCCITM database
KCCITM_TEST_SUITE = [
    # (question, difficulty, expected_non_empty, description)
    # Simple
    ("how many students are there", "simple", True, "basic count"),
    ("how many CSE students are there", "simple", True, "count with branch filter"),
    ("top 5 students by SGPA in semester 1", "simple", True, "ranking with semester filter"),
    ("average SGPA in semester 4", "simple", True, "aggregate avg"),
    # Medium
    ("which branch has the highest average SGPA across all semesters", "medium", True, "group by + order"),
    ("male vs female average SGPA in semester 4", "medium", True, "gender comparison"),
    ("grade distribution in semester 4", "medium", True, "group by grade"),
    ("students from batch 2021 with back papers", "medium", True, "batch filter + join"),
    ("compare semester 1 and semester 4 average SGPA for CSE", "medium", True, "multi-filter comparison"),
    # Hard
    ("what percentage of female students passed all semesters", "hard", True, "percentage + subquery"),
    ("subjects where more than 30 percent of students got grade F", "hard", True, "having clause + percentage"),
    ("compare pass rates between batch 2021 and batch 2022", "hard", True, "batch comparison + rates"),
    ("which subjects have more than 10 percent failure rate", "hard", True, "failure rate calculation"),
    ("how many students scored grade A+ in each semester", "hard", True, "cross-semester aggregation"),
    # Very Hard (LogicCat-level reasoning)
    ("students whose SGPA improved every semester", "very_hard", True, "self-join + consecutive comparison"),
    ("bottom 5 students by total marks in semester 3", "very_hard", True, "reverse ranking"),
]


@dataclass
class EvalResult:
    question: str
    difficulty: str
    sql_generated: str = ""
    execution_success: bool = False
    row_count: int = 0
    retries_used: int = 0
    time_ms: float = 0.0
    error: str = ""
    warnings: list[str] = field(default_factory=list)


async def evaluate_kccitm(verbose: bool = False) -> list[EvalResult]:
    """Run the built-in KCCITM test suite."""
    from core.llm_client import OllamaClient
    from core.sql_pipeline import SQLPipeline
    from core.router import RouteResult

    llm = OllamaClient()
    pipeline = SQLPipeline(llm)
    results: list[EvalResult] = []

    print(f"\n\033[94mKCCITM Text-to-SQL Evaluation ({len(KCCITM_TEST_SUITE)} queries)\033[0m\n")

    for i, (question, difficulty, expect_results, desc) in enumerate(KCCITM_TEST_SUITE, 1):
        eval_r = EvalResult(question=question, difficulty=difficulty)
        t0 = time.time()

        try:
            # Create a minimal RouteResult
            route = RouteResult(route="SQL", needs_filter=False, intent=question, confidence=0.95)
            result = await pipeline.run(question, route)

            eval_r.time_ms = (time.time() - t0) * 1000
            eval_r.sql_generated = result.sql
            eval_r.execution_success = result.success
            eval_r.row_count = result.row_count
            eval_r.retries_used = result.retries_used
            eval_r.warnings = result.verification_warnings
            eval_r.error = result.error

            # Evaluate
            passed = result.success and (not expect_results or result.row_count > 0)
            icon = "\033[92m✓\033[0m" if passed else "\033[91m✗\033[0m"
            retry_info = f" (retries: {result.retries_used})" if result.retries_used > 0 else ""
            print(f"  {icon} [{difficulty:>9}] {question[:55]:<55} → {result.row_count:>3} rows | {eval_r.time_ms:>7.0f}ms{retry_info}")

            if verbose and result.sql:
                print(f"    SQL: {result.sql[:120]}")
            if result.error:
                print(f"    Error: {result.error[:100]}")
            if result.verification_warnings:
                for w in result.verification_warnings:
                    print(f"    ⚠ {w[:100]}")

        except Exception as e:
            eval_r.time_ms = (time.time() - t0) * 1000
            eval_r.error = str(e)
            print(f"  \033[91m✗\033[0m [{difficulty:>9}] {question[:55]:<55} → ERROR: {str(e)[:80]}")

        results.append(eval_r)

    # Summary
    total = len(results)
    passed = sum(1 for r in results if r.execution_success and r.row_count > 0)
    gen_ok = sum(1 for r in results if r.sql_generated)
    exec_ok = sum(1 for r in results if r.execution_success)
    avg_time = sum(r.time_ms for r in results) / total if total else 0
    retried = sum(1 for r in results if r.retries_used > 0)

    # By difficulty
    by_diff: dict[str, list[EvalResult]] = {}
    for r in results:
        by_diff.setdefault(r.difficulty, []).append(r)

    print(f"\n{'='*60}")
    print(f"\033[94mRESULTS\033[0m")
    print(f"{'='*60}")
    print(f"  Total queries:     {total}")
    print(f"  SQL generated:     {gen_ok}/{total} ({gen_ok/total*100:.0f}%)")
    print(f"  Execution success: {exec_ok}/{total} ({exec_ok/total*100:.0f}%)")
    print(f"  Non-empty results: {passed}/{total} ({passed/total*100:.0f}%)")
    print(f"  Needed retries:    {retried}/{total}")
    print(f"  Avg time:          {avg_time:.0f}ms")
    print()
    for diff in ["simple", "medium", "hard", "very_hard"]:
        if diff in by_diff:
            d_results = by_diff[diff]
            d_pass = sum(1 for r in d_results if r.execution_success and r.row_count > 0)
            print(f"  {diff:>10}: {d_pass}/{len(d_results)} ({d_pass/len(d_results)*100:.0f}%)")
    print(f"{'='*60}")

    # Save results
    output = {
        "total": total,
        "sql_generated": gen_ok,
        "execution_success": exec_ok,
        "non_empty_results": passed,
        "avg_time_ms": round(avg_time, 1),
        "by_difficulty": {
            diff: {
                "total": len(rs),
                "passed": sum(1 for r in rs if r.execution_success and r.row_count > 0),
            }
            for diff, rs in by_diff.items()
        },
        "details": [
            {
                "question": r.question,
                "difficulty": r.difficulty,
                "sql": r.sql_generated,
                "success": r.execution_success,
                "rows": r.row_count,
                "retries": r.retries_used,
                "time_ms": round(r.time_ms, 1),
                "error": r.error,
                "warnings": r.warnings,
            }
            for r in results
        ],
    }

    Path("data").mkdir(exist_ok=True)
    with open("data/logiccat_results.json", "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to data/logiccat_results.json")

    return results


async def evaluate_external(dataset_path: str, verbose: bool = False):
    """Evaluate against an external dataset (LogicCat JSON format)."""
    from core.llm_client import OllamaClient
    from core.sql_pipeline import SQLPipeline
    from core.router import RouteResult

    with open(dataset_path) as f:
        dataset = json.load(f)

    total = len(dataset)
    generated = 0
    errors = []

    print(f"\nEvaluating {total} queries from {dataset_path}...")

    llm = OllamaClient()
    pipeline = SQLPipeline(llm)

    for i, item in enumerate(dataset):
        question = item.get("question", "")
        difficulty = item.get("difficulty", "unknown")

        try:
            route = RouteResult(route="SQL", needs_filter=False, intent=question, confidence=0.95)
            result = await pipeline.run(question, route)

            if result.success and result.sql:
                generated += 1
                if verbose:
                    print(f"  [{i+1}/{total}] [{difficulty}] OK: {result.sql[:80]}")
            else:
                errors.append({"question": question, "error": result.error})
                if verbose:
                    print(f"  [{i+1}/{total}] [{difficulty}] FAIL: {result.error[:80]}")
        except Exception as e:
            errors.append({"question": question, "error": str(e)})

    print(f"\n=== RESULTS ===")
    print(f"Total: {total}")
    print(f"Generated: {generated} ({generated/total*100:.1f}%)")
    print(f"Failed: {len(errors)}")

    output = {"total": total, "generated": generated, "failed": len(errors), "errors": errors[:20]}
    with open("data/logiccat_results.json", "w") as f:
        json.dump(output, f, indent=2)
    print(f"Results saved to data/logiccat_results.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KCCITM Text-to-SQL Evaluation")
    parser.add_argument("--dataset", help="Path to external dataset JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show generated SQL")
    args = parser.parse_args()

    if args.dataset:
        asyncio.run(evaluate_external(args.dataset, args.verbose))
    else:
        asyncio.run(evaluate_kccitm(args.verbose))

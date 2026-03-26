"""
SQLValidator unit tests — no external dependencies.

Tests all 18 validation cases covering: SELECT-only enforcement,
forbidden keyword detection, comment blocking, multi-statement
rejection, JOIN/subquery limits, and LIMIT enforcement.

Run:
    cd backend
    python -m tests.test_sql_validator
"""

from core.sql_pipeline import SQLValidator

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"


# ── Test cases ─────────────────────────────────────────────────────────────────
# Format: (sql, expect_error: bool, description)

VALIDATE_CASES = [
    # ── Valid SELECTs ──────────────────────────────────────────────────────────
    (
        "SELECT s.name, sr.sgpa FROM students s JOIN semester_results sr ON s.roll_no = sr.roll_no WHERE sr.semester = 4 ORDER BY sr.sgpa DESC LIMIT 5",
        False,
        "Valid SELECT with JOIN, ORDER BY, LIMIT",
    ),
    (
        "SELECT COUNT(*) FROM students WHERE branch = 'COMPUTER SCIENCE AND ENGINEERING'",
        False,
        "Valid COUNT query",
    ),
    (
        "WITH ranked AS (SELECT * FROM students) SELECT * FROM ranked LIMIT 10",
        False,
        "Valid CTE (WITH clause)",
    ),
    (
        "SELECT s.name, AVG(sr.sgpa) FROM students s JOIN semester_results sr ON s.roll_no = sr.roll_no GROUP BY s.roll_no LIMIT 50",
        False,
        "Valid aggregate with GROUP BY",
    ),
    (
        "SELECT * FROM students LIMIT 100",
        False,
        "Valid SELECT at max LIMIT boundary",
    ),

    # ── Non-SELECT statements ──────────────────────────────────────────────────
    (
        "INSERT INTO students (roll_no, name) VALUES ('123', 'Test')",
        True,
        "INSERT rejected",
    ),
    (
        "UPDATE students SET name = 'x' WHERE roll_no = '1'",
        True,
        "UPDATE rejected",
    ),
    (
        "DELETE FROM students WHERE roll_no = '1'",
        True,
        "DELETE rejected",
    ),
    (
        "DROP TABLE students",
        True,
        "DROP rejected",
    ),

    # ── Forbidden keyword injection ────────────────────────────────────────────
    (
        "SELECT * FROM students WHERE name = 'x'; DROP TABLE students",
        True,
        "Inline DROP via semicolon rejected",
    ),
    (
        "SELECT SLEEP(5) FROM students LIMIT 1",
        True,
        "SLEEP() injection rejected",
    ),
    (
        "SELECT * FROM INFORMATION_SCHEMA.TABLES LIMIT 5",
        True,
        "INFORMATION_SCHEMA access rejected",
    ),
    (
        "SELECT * FROM students -- ignore filters",
        True,
        "SQL comment (--) rejected",
    ),
    (
        "SELECT * FROM students /* comment */ LIMIT 5",
        True,
        "SQL block comment (/* */) rejected",
    ),

    # ── Structural limits ──────────────────────────────────────────────────────
    (
        "SELECT * FROM students s "
        "JOIN semester_results sr ON s.roll_no = sr.roll_no "
        "JOIN subject_marks sm ON s.roll_no = sm.roll_no "
        "JOIN students s2 ON s.roll_no = s2.roll_no "
        "JOIN semester_results sr2 ON sr2.roll_no = s.roll_no LIMIT 10",
        True,
        "Too many JOINs (4) rejected",
    ),
    (
        "SELECT * FROM (SELECT * FROM (SELECT * FROM (SELECT * FROM students LIMIT 5) t1 LIMIT 5) t2 LIMIT 5) t3 LIMIT 5",
        True,
        "Too many subqueries (3) rejected",
    ),
    (
        "SELECT * FROM students LIMIT 200",
        True,
        "LIMIT > 100 rejected",
    ),

    # ── enforce_limit helper ───────────────────────────────────────────────────
    (
        "SELECT * FROM students LIMIT 500",
        True,
        "LIMIT 500 fails validate() (enforce_limit test is separate)",
    ),
]

ENFORCE_LIMIT_CASES = [
    ("SELECT * FROM students LIMIT 200", 100, "SELECT * FROM students LIMIT 100"),
    ("SELECT * FROM students LIMIT 50", 100, "SELECT * FROM students LIMIT 50"),
    ("SELECT * FROM students", 100, "SELECT * FROM students LIMIT 100"),
    ("SELECT * FROM students;", 100, "SELECT * FROM students LIMIT 100"),
]


# ── Runner ─────────────────────────────────────────────────────────────────────

def run_tests() -> None:
    passed = 0
    failed = 0

    # ── Validate tests ─────────────────────────────────────────────────────────
    print("=" * 70)
    print("SQLValidator.validate() — 18 cases")
    print("=" * 70)

    for sql, expect_error, description in VALIDATE_CASES:
        error = SQLValidator.validate(sql)
        got_error = error is not None

        if got_error == expect_error:
            passed += 1
            outcome = "error as expected" if expect_error else "valid as expected"
            print(f"{GREEN}✓ {description}{RESET}")
            print(f"  {outcome}: {error or 'no error'}")
        else:
            failed += 1
            print(f"{RED}✗ {description}{RESET}")
            if expect_error:
                print(f"  Expected error but got: None (query was accepted)")
            else:
                print(f"  Expected no error but got: {error}")
        print()

    # ── enforce_limit tests ────────────────────────────────────────────────────
    print("=" * 70)
    print("SQLValidator.enforce_limit() — 4 cases")
    print("=" * 70)

    for sql, max_limit, expected in ENFORCE_LIMIT_CASES:
        result = SQLValidator.enforce_limit(sql, max_limit)
        if result == expected:
            passed += 1
            print(f"{GREEN}✓ enforce_limit: {sql[:50]!r}{RESET}")
            print(f"  → {result!r}")
        else:
            failed += 1
            print(f"{RED}✗ enforce_limit: {sql[:50]!r}{RESET}")
            print(f"  Expected: {expected!r}")
            print(f"  Got:      {result!r}")
        print()

    # ── Summary ────────────────────────────────────────────────────────────────
    total = passed + failed
    pct = (passed / total * 100) if total else 0.0
    print("=" * 70)
    print(f"Validator Test Results: {passed}/{total} passed ({pct:.0f}%)")
    print("=" * 70)

    if pct == 100.0:
        print(f"\n{GREEN}✓ All validator tests passed!{RESET}")
    else:
        print(f"\n{RED}✗ {failed} test(s) failed.{RESET}")


if __name__ == "__main__":
    run_tests()

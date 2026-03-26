# Testing Patterns

**Analysis Date:** 2026-03-11

## Test Framework

**Runner:**
- None. No test framework is installed or configured.
- `requirements.txt` contains only: `streamlit`, `sentence-transformers`, `faiss-cpu`, `mysql-connector-python`, `pandas`
- No `pytest`, `unittest`, `nose`, or any test runner present

**Assertion Library:**
- None

**Run Commands:**
```bash
# No test commands available
# The only "test" file is:
python test_mysql.py   # Manual DB connectivity check only
```

## Test File Organization

**Location:**
- `test_mysql.py` exists at the project root but is a connectivity diagnostic script, not a test suite
- No `tests/` directory
- No `test_*.py` files beyond `test_mysql.py`
- No co-located `*_test.py` or `*.spec.py` files

**What `test_mysql.py` does:**
- Opens a MySQL connection (with hardcoded credentials to a different database than production)
- Runs `SHOW TABLES` and prints results
- No assertions, no test cases, no pass/fail reporting

## Test Structure

**Suite Organization:**
- Not applicable. No test suites exist.

**Patterns:**
- No setup/teardown patterns
- No fixtures
- No assertion patterns

## Mocking

**Framework:** None

**Patterns:**
- No mocking used anywhere
- DB calls are made directly against a live MySQL instance
- No dependency injection to enable substituting test doubles

## Fixtures and Factories

**Test Data:**
- None. No fixture files, factory functions, or seed scripts.

**Location:**
- Not applicable

## Coverage

**Requirements:** None enforced

**View Coverage:**
```bash
# Not configured
```

## Test Types

**Unit Tests:**
- None present

**Integration Tests:**
- None present. `test_mysql.py` is the closest analog — a manual smoke test for DB connectivity.

**E2E Tests:**
- Not used

## What Would Need to Be Tested

The following logic in `db_marks.py` and `app.py` is completely untested:

**`db_marks.py`:**
- `safe_float()` — edge cases with None, empty string, invalid values
- `calculate_subject_total()` — combinations of None/valid internal/external
- `derive_batch_from_roll()` — roll number parsing edge cases
- `normalize_name()` — whitespace and casing normalization
- `get_best_or_weakest_subject()` — mode switching, empty DataFrame handling
- `calculate_average_marks()` — None propagation, empty subject list
- `calculate_percentage()` — division by zero guard at `max_total == 0`
- `search_students_by_name()` — scoring logic: exact/contains/token match
- `get_subject_toppers()` — semester filtering, subject string matching
- `get_batch_toppers_by_cgpa()` — CGPA derivation from SGPA list

**`app.py`:**
- `detect_roll_number()` — regex matching for 6+ digit sequences
- `extract_semester()` — multiple regex patterns
- `extract_batch()` — 4-digit and 2-digit batch year patterns
- `is_*_query()` family — keyword detection predicates
- `replace_subject_aliases()` — alias substitution via `SUBJECT_ALIASES`
- `extract_subject_keywords()` — stop word filtering
- `extract_name_candidate()` — multi-regex cleanup pipeline
- `apply_semester_filter()` — DataFrame filtering
- `resolve_pending_name_selection()` — digit vs name disambiguation
- `execute_student_query()` — full query dispatch logic
- `handle_db_query()` — routing between topper/name/roll paths

## Common Patterns

**Async Testing:**
- Not applicable (synchronous codebase)

**Error Testing:**
- Not applicable (no test framework)

## Notes for Adding Tests

The pure helper functions in both files are the easiest starting point since they have no DB dependency:

- `safe_float()`, `calculate_subject_total()`, `derive_batch_from_roll()`, `normalize_name()` in `db_marks.py`
- All `is_*_query()`, `extract_*()`, `detect_roll_number()`, `normalize_text()`, `normalize_name()` in `app.py`

DB-dependent functions (`get_marks()`, `search_students_by_name()`, etc.) would require either a test MySQL instance or mocking of `get_connection()`. Currently `get_connection()` is called directly inside each function with no injection point, making mocking require patching at the module level (e.g., `unittest.mock.patch("db_marks.get_connection")`).

Streamlit-specific code (`process_query()`, `handle_db_query()` in app context, sidebar rendering) would require the `streamlit` test utilities or extraction of logic away from `st.session_state` side effects.

---

*Testing analysis: 2026-03-11*

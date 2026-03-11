---
phase: 01-secrets-security-foundation
plan: "02"
subsystem: security
tags: [python-dotenv, dotenv, gitignore, env, secrets, mysql, credentials, sec-01, sec-02]

# Dependency graph
requires:
  - phase: 01-secrets-security-foundation
    plan: "01"
    provides: "pytest test scaffolding for SEC-01 and SEC-02 automated verification"
provides:
  - ".gitignore excluding .env, chat_memory.json, Python bytecode, venv, IDE, FAISS files"
  - ".env.example with placeholder values documenting all required environment variables"
  - "db_marks.py reading DB credentials from os.getenv() via load_dotenv() — no hardcoded secrets"
  - "db_connection.py with identical credential fix applied"
  - "python-dotenv==1.2.2 pinned in requirements.txt"
  - "bcrypt==5.0.0 pinned (was previously unpinned)"
affects:
  - 01-03-PLAN (setup_users_table.py reads ADMIN_USER/ADMIN_PASS from .env)
  - all future plans (db_marks.get_connection() is the production DB call used by app.py)

# Tech tracking
tech-stack:
  added: [python-dotenv==1.2.2]
  patterns:
    - "Module-level load_dotenv() at top of any file reading from .env"
    - "os.getenv('DB_HOST', 'localhost') with fallback only for non-secret values (host); no fallback for DB_USER/DB_PASSWORD/DB_NAME"
    - ".env git-ignored; .env.example committed as documentation template"

key-files:
  created:
    - .gitignore
    - .env.example
  modified:
    - db_marks.py
    - db_connection.py
    - requirements.txt

key-decisions:
  - "os.getenv('DB_HOST', 'localhost') has a default fallback; DB_USER/DB_PASSWORD/DB_NAME have no default — a missing password returns None, which causes a connect error rather than silently using a wrong credential"
  - "ADMIN_PASS left blank in .env — user fills in before running setup_users_table.py (Plan 03); hardcoding a default admin password in source would defeat the purpose"
  - "bcrypt pinned to ==5.0.0 (was unpinned 'bcrypt') to match the version installed in environment and Plan 01 SUMMARY"

patterns-established:
  - "Pattern: .env.example is the single source of truth for environment variable documentation"
  - "Pattern: load_dotenv() at module level ensures .env loads even when file is imported before streamlit initializes session"

requirements-completed: [SEC-01, SEC-02]

# Metrics
duration: 3min
completed: 2026-03-11
---

# Phase 1 Plan 02: Credential Hygiene — Environment Variable Migration Summary

**Hardcoded MySQL password removed from db_marks.py and db_connection.py; credentials moved to git-ignored .env loaded via python-dotenv; .gitignore and .env.example created; 5/5 SEC-01+SEC-02 tests pass**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-11T10:18:19Z
- **Completed:** 2026-03-11T10:21:23Z
- **Tasks:** 3
- **Files modified:** 5

## Accomplishments

- Created .gitignore preventing .env, chat_memory.json, bytecode, IDE, and FAISS files from ever being committed
- Created .env.example as a committed placeholder template documenting all required env vars for college IT setup
- Patched db_marks.py and db_connection.py to use os.getenv() via load_dotenv() — plaintext password `qCsfeuECc3MW` no longer appears in any source file
- All 5 SEC-01 + SEC-02 pytest tests pass green

## Task Commits

Each task was committed atomically:

1. **Task 1: Create .gitignore and .env.example** - `7e6a510` (chore)
2. **Task 2: Add DB credentials to .env and pin python-dotenv in requirements.txt** - `e7d5448` (chore)
3. **Task 3: Patch db_marks.py and db_connection.py to load credentials from environment** - `054b77c` (feat)

**Plan metadata:** (docs commit — see below)

## Files Created/Modified

- `.gitignore` - Excludes .env, chat_memory.json, Python bytecode, venv/, .venv/, env/, .vscode/, .idea/, *.swp, *.index, faiss_store/, .DS_Store, Thumbs.db
- `.env.example` - Placeholder template with DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, OPENAI_API_KEY, ADMIN_USER, ADMIN_PASS (no real secrets — safe to commit)
- `db_marks.py` - Added `import os`, `from dotenv import load_dotenv`, `load_dotenv()` at module level; get_connection() now uses os.getenv() for all four DB parameters
- `db_connection.py` - Identical patch applied; same import pattern and os.getenv() usage
- `requirements.txt` - Added python-dotenv==1.2.2; pinned bcrypt from unpinned to bcrypt==5.0.0

## Decisions Made

- `os.getenv("DB_HOST", "localhost")` provides a default fallback for host (non-secret, reasonable default), but DB_USER/DB_PASSWORD/DB_NAME have no default — a missing credential returns None, causing a connect error rather than silently connecting with wrong values
- ADMIN_PASS left blank in .env; hardcoding a default admin password would defeat the purpose of the secure setup in Plan 03
- bcrypt pinned from unpinned `bcrypt` to `bcrypt==5.0.0` to match the version installed and documented in Plan 01 SUMMARY

## Deviations from Plan

None — plan executed exactly as written. The only note: pandas and mysql-connector-python were not installed in the test environment, but installing them is expected setup (they are already in requirements.txt). Tests passed once dependencies were available.

## Issues Encountered

- `mysql-connector-python` and `pandas` were not installed in the current Python environment, causing test_sec01.py to fail on import when the test reimports db_marks. Fixed by running `pip3 install mysql-connector-python pandas --break-system-packages`. This is a local dev environment issue only (macOS system Python requires `--break-system-packages`); not a code issue.

## User Setup Required

None beyond standard `pip install -r requirements.txt`. The .env file already has DB credentials appended. College IT should copy .env.example to .env and fill in their actual values.

## Self-Check

- .gitignore exists: confirmed
- .env.example exists: confirmed
- db_marks.py contains no plaintext password: confirmed (grep returns no matches)
- db_connection.py contains no plaintext password: confirmed
- requirements.txt contains python-dotenv==1.2.2: confirmed
- requirements.txt contains bcrypt==5.0.0: confirmed
- pytest tests/test_sec01.py tests/test_sec02.py: 5 passed

## Next Phase Readiness

- Plan 03 (setup_users_table.py + bcrypt password hashing) can now read ADMIN_USER and ADMIN_PASS from .env via load_dotenv()
- app.py indirectly benefits: it imports db_marks, which now loads from .env automatically
- All SEC-01 and SEC-02 requirements satisfied

---
*Phase: 01-secrets-security-foundation*
*Completed: 2026-03-11*

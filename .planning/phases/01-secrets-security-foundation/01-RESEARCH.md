# Phase 1: Secrets & Security Foundation - Research

**Researched:** 2026-03-11
**Domain:** Python secrets management (python-dotenv), bcrypt password hashing, MySQL DDL, .gitignore
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- DB vars named: `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`
- Only DB credentials added to `.env` this phase (OPENAI_API_KEY is already there)
- Create `.env.example` now with placeholder values — documents required vars for college IT
- Update `db_marks.py` and `db_connection.py` independently (not via shared module)
- Each file reads from `.env` using python-dotenv `load_dotenv()` + `os.getenv()`
- Prototype files (sql_executor.py, db_agent.py, test_mysql.py) — leave unchanged
- Add `python-dotenv` to `requirements.txt` with a pinned version
- Table goes in existing `kccitm` database
- Columns: `id` (AUTO_INCREMENT PK), `username` (VARCHAR UNIQUE NOT NULL), `password_hash` (VARCHAR NOT NULL), `role` (ENUM('admin','faculty') NOT NULL), `is_active` (BOOLEAN DEFAULT TRUE), `created_at` (TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
- Seed initial admin user from `.env` vars: `ADMIN_USER` and `ADMIN_PASS` (bcrypt-hash the password during seeding)
- Add `ADMIN_USER` and `ADMIN_PASS` to `.env.example`
- Add `.env` and `chat_memory.json` to `.gitignore` (SEC-02 requirements)
- Also add standard Python ignores: `__pycache__/`, `*.pyc`, `*.pyo`, `.env`

### Claude's Discretion
- bcrypt work factor / rounds (standard default is fine)
- Whether to use a Python setup script or raw SQL file for the users table creation
- Exact python-dotenv version pin
- Whether .env.example includes comments explaining each var
- What else to include in .gitignore (e.g., FAISS files, docker volumes, IDE files)

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| SEC-01 | All DB passwords and secrets loaded from `.env` (no hardcoded credentials in source) | python-dotenv 1.2.2 `load_dotenv()` + `os.getenv()` pattern; both `db_marks.py` and `db_connection.py` have single `get_connection()` that contains the hardcoded credentials — one targeted swap per file |
| SEC-02 | `.env` and `chat_memory.json` added to `.gitignore` | No `.gitignore` exists yet; create fresh file before any `git init`; standard Python template entries well-documented |
| SEC-03 | Passwords stored with bcrypt hashing in MySQL users table | bcrypt 5.0.0; `gensalt()` default rounds=12; `VARCHAR(60)` column is sufficient for bcrypt hash output; Python setup script can CREATE TABLE + INSERT seed user in one run |
</phase_requirements>

---

## Summary

Phase 1 is a pure infrastructure change with no user-facing output. The work divides into three independent tracks: (1) remove hardcoded credentials from two source files by loading them from `.env` via python-dotenv, (2) create `.gitignore` to protect `.env` and `chat_memory.json` from version control, and (3) create the `users` table in MySQL with a bcrypt-hashed seed admin account.

The credential removal is the most directly verifiable deliverable — `grep -r "password" .` must return no plaintext strings in tracked files. Both files that need changing (`db_marks.py` and `db_connection.py`) follow the identical pattern: `mysql.connector.connect(host=..., password="qCsfeuECc3MW")`. The fix is the same in each: add `load_dotenv()` at module load time and replace the string literals with `os.getenv("DB_HOST")`, etc.

The `users` table and seeding script complete SEC-03. bcrypt 5.0.0 (latest) handles password hashing; a single Python script can create the table and insert the hashed admin row in one execution. The project is not yet a git repo, so no history cleanup is required — `.gitignore` just needs to exist before `git init`.

**Primary recommendation:** Use python-dotenv 1.2.2 for credential loading; bcrypt 5.0.0 for password hashing; create table via a Python setup script (not raw SQL file) so bcrypt hashing of the seed password happens in Python where the library already exists.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| python-dotenv | 1.2.2 | Load `.env` file into `os.environ` at runtime | De-facto Python standard for 12-factor secrets management; zero runtime overhead |
| bcrypt | 5.0.0 | Hash and verify passwords | Adaptive work factor, resistant to GPU attacks; explicitly endorsed by Python security community |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| os (stdlib) | — | `os.getenv()` reads env vars after `load_dotenv()` | Always — no extra dependency needed |
| mysql-connector-python | already in requirements.txt | DDL execution for `CREATE TABLE` / `INSERT` | Already present; use for setup script |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| python-dotenv | pydantic-settings | pydantic-settings adds type validation but requires pydantic as dependency — overkill for a single-file credential swap |
| bcrypt | passlib | passlib wraps bcrypt and others but adds a layer of abstraction not needed here; bcrypt directly is simpler |
| bcrypt | argon2-cffi | argon2id is theoretically stronger but bcrypt 5.0.0 maintainers confirm bcrypt remains acceptable; no existing argon2 familiarity in this project |

**Installation:**
```bash
pip install python-dotenv==1.2.2 bcrypt==5.0.0
```

---

## Architecture Patterns

### Recommended Project Structure (Phase 1 changes only)

```
rag-zip/
├── .env                  # real secrets — git-ignored
├── .env.example          # placeholder template — committed to git
├── .gitignore            # new file — protects .env and chat_memory.json
├── db_marks.py           # patched: load_dotenv() + os.getenv() in get_connection()
├── db_connection.py      # patched: same fix as db_marks.py
├── requirements.txt      # add python-dotenv==1.2.2 and bcrypt==5.0.0
└── setup_users_table.py  # new: creates users table + seeds admin
```

### Pattern 1: Loading .env in a module

**What:** Call `load_dotenv()` once at module import time, then use `os.getenv()` inside the connection function.
**When to use:** Every file that needs env vars. Calling `load_dotenv()` multiple times is safe — it is idempotent.

```python
# Source: https://pypi.org/project/python-dotenv/ (v1.2.2 docs)
import os
from dotenv import load_dotenv

load_dotenv()  # reads .env file, sets os.environ (does NOT override existing env vars)

def get_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME")
    )
```

**Note:** The `"localhost"` default for `DB_HOST` means local dev still works if `.env` is incomplete, but password will be `None` if `DB_PASSWORD` is missing — which will cause a MySQL connector error immediately, not silently.

### Pattern 2: Hashing a password with bcrypt

**What:** Hash a plaintext password once (during setup/seeding); store the hash. Never store plaintext.
**When to use:** Any time a password is set or created — never at login verification time.

```python
# Source: https://pypi.org/project/bcrypt/ (v5.0.0 docs)
import bcrypt

plaintext = b"the_admin_password"          # must be bytes
hashed = bcrypt.hashpw(plaintext, bcrypt.gensalt())  # rounds=12 default
# hashed is bytes, e.g. b"$2b$12$..."
# Store hashed.decode("utf-8") in VARCHAR(60) column
```

### Pattern 3: Verifying a password with bcrypt

**What:** Compare a login attempt against the stored hash.
**When to use:** Phase 2 (login). Documented here because setup script must prove round-trip works.

```python
# Source: https://pypi.org/project/bcrypt/ (v5.0.0 docs)
stored_hash = b"$2b$12$..."   # retrieved from DB as bytes (or encode if str)
if bcrypt.checkpw(b"entered_password", stored_hash):
    print("Password matches")
```

### Pattern 4: Setup script structure

**What:** Single Python script that creates the `users` table and inserts the seed admin.
**When to use:** Run once during initial deployment or after schema wipe. Safe to add `IF NOT EXISTS` guard so re-running does not error.

```python
# Pseudocode outline — source: synthesized from MySQL connector docs + bcrypt docs
import os, bcrypt
from dotenv import load_dotenv
import mysql.connector

load_dotenv()

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    username     VARCHAR(100) NOT NULL UNIQUE,
    password_hash VARCHAR(60) NOT NULL,
    role         ENUM('admin', 'faculty') NOT NULL,
    is_active    BOOLEAN DEFAULT TRUE,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

admin_user = os.getenv("ADMIN_USER")
admin_pass = os.getenv("ADMIN_PASS").encode("utf-8")
hashed = bcrypt.hashpw(admin_pass, bcrypt.gensalt())

INSERT_ADMIN_SQL = """
INSERT IGNORE INTO users (username, password_hash, role)
VALUES (%s, %s, 'admin')
"""
# Use INSERT IGNORE so re-running the script doesn't duplicate the admin row
```

### Anti-Patterns to Avoid

- **Fallback to plaintext:** Do not write `os.getenv("DB_PASSWORD", "qCsfeuECc3MW")` — a default that leaks the real password defeats the purpose.
- **Encoding mismatch:** bcrypt requires `bytes` input. Passing a plain `str` to `hashpw()` raises `TypeError` in bcrypt 5.0.0. Always `.encode("utf-8")` first.
- **Column too small for hash:** bcrypt output is always 60 characters. `VARCHAR(60)` is the minimum safe column size.
- **Committing .env before .gitignore:** If `.gitignore` is written after `git init` and a commit is made, `.env` may already be tracked. Since this project is not yet a git repo, create `.gitignore` before running `git init`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Password hashing | SHA-256/MD5 loops, custom salting | `bcrypt.hashpw()` | bcrypt's adaptive cost factor; plain hash functions are vulnerable to rainbow tables and GPU attacks |
| Secrets loading | `configparser`, manual file parsing, `os.environ` hardcoding | `python-dotenv` | Handles quoting, comment stripping, variable expansion, and `.env` search-path logic correctly |
| "Is password correct?" | String comparison of hashes | `bcrypt.checkpw()` | Timing-safe comparison built in; naive `==` comparison on hash strings is safe for bcrypt but `checkpw` is the correct API |

**Key insight:** bcrypt handles all the salt, rounds, and format bookkeeping internally. Feeding it a raw string or trying to build a custom scheme produces insecure or fragile code. The library is 3 lines of code at point of use.

---

## Common Pitfalls

### Pitfall 1: os.getenv() returns None silently

**What goes wrong:** If `.env` is missing or a variable name is misspelled, `os.getenv("DB_PASSWORD")` returns `None`. MySQL connector accepts `None` for password and may connect to databases with no root password set, masking the configuration error until production.
**Why it happens:** `os.getenv()` has no required-key semantics.
**How to avoid:** After `load_dotenv()`, add a guard that raises `EnvironmentError` if critical vars are None — or use `os.environ["DB_PASSWORD"]` (raises `KeyError` immediately if missing).
**Warning signs:** App starts locally but fails with cryptic MySQL auth error on a fresh checkout.

### Pitfall 2: bcrypt requires bytes, not str

**What goes wrong:** `bcrypt.hashpw("mypassword", bcrypt.gensalt())` raises `TypeError: Unicode-objects must be encoded before hashing`.
**Why it happens:** bcrypt 5.0.0 enforces bytes input.
**How to avoid:** Always encode: `password.encode("utf-8")` before passing to `hashpw` or `checkpw`.
**Warning signs:** `TypeError` at setup script execution time — caught immediately.

### Pitfall 3: .env tracked by git before .gitignore exists

**What goes wrong:** Developer runs `git init && git add . && git commit` before creating `.gitignore` — `.env` enters git history with the real OpenAI API key.
**Why it happens:** `.gitignore` only prevents tracking of not-yet-tracked files.
**How to avoid:** Create `.gitignore` as the very first file. Since the project is not yet a git repo, the order is: write `.gitignore` → `git init` → `git add` → never adds `.env`.
**Warning signs:** `git ls-files | grep .env` shows `.env` in the index.

### Pitfall 4: INSERT IGNORE omitted from seed script

**What goes wrong:** Running `setup_users_table.py` twice throws `IntegrityError: Duplicate entry 'admin' for key 'username'`.
**Why it happens:** `INSERT` without `IGNORE` is not idempotent.
**How to avoid:** Use `INSERT IGNORE INTO users ...` so the script can be re-run safely.

### Pitfall 5: load_dotenv() called after mysql.connector.connect()

**What goes wrong:** If `get_connection()` is called at module import time (e.g., as a module-level variable), `load_dotenv()` at the top of the file hasn't set env vars yet because Python executes top-level code sequentially.
**Why it happens:** In this codebase, `get_connection()` is called lazily (inside functions), so this is not an issue. But moving `load_dotenv()` after the import block is fine; it must come before any `os.getenv()` call.
**Warning signs:** `os.getenv("DB_PASSWORD")` returns `None` even though `.env` exists.

---

## Code Examples

Verified patterns from official sources:

### Final db_marks.py get_connection() after patch

```python
# Source: https://pypi.org/project/python-dotenv/ (1.2.2)
import os
from dotenv import load_dotenv
import mysql.connector

load_dotenv()

def get_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME")
    )
```

### .env additions for this phase

```
# Existing (already in .env):
OPENAI_API_KEY=sk-proj-...

# Add in Phase 1:
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=qCsfeuECc3MW
DB_NAME=kccitm
ADMIN_USER=admin
ADMIN_PASS=<choose_strong_password>
```

### .env.example (committed to git — no real values)

```
# Database connection
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=your_db_password_here
DB_NAME=kccitm

# OpenAI (if using LLM features)
OPENAI_API_KEY=sk-proj-...

# Initial admin account (used only by setup_users_table.py)
ADMIN_USER=admin
ADMIN_PASS=choose_a_strong_password
```

### .gitignore (complete file for this phase)

```gitignore
# Secrets — never commit
.env

# Application data files
chat_memory.json

# Python
__pycache__/
*.pyc
*.pyo
*.pyd
.Python

# Virtual environments
venv/
.venv/
env/

# IDE
.vscode/
.idea/
*.swp

# FAISS index files (large binary, regenerable)
*.index
faiss_store/

# OS
.DS_Store
Thumbs.db
```

### users table CREATE statement

```sql
-- Source: MySQL 8.0 reference for ENUM, BOOLEAN, AUTO_INCREMENT
CREATE TABLE IF NOT EXISTS users (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(100) NOT NULL UNIQUE,
    password_hash VARCHAR(60)  NOT NULL,
    role          ENUM('admin', 'faculty') NOT NULL,
    is_active     BOOLEAN DEFAULT TRUE,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### bcrypt hash + verify round-trip

```python
# Source: https://pypi.org/project/bcrypt/ (5.0.0)
import bcrypt

# Hashing (at account creation / seeding):
plain = "my_password".encode("utf-8")
hashed = bcrypt.hashpw(plain, bcrypt.gensalt())   # rounds=12 default
stored = hashed.decode("utf-8")   # store this string in VARCHAR(60)

# Verification (at login, Phase 2):
entered = "my_password".encode("utf-8")
is_valid = bcrypt.checkpw(entered, stored.encode("utf-8"))
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Hardcoded credentials | `.env` + python-dotenv | Standard since 2015 (12-factor app) | Required for any deployment beyond one laptop |
| MD5/SHA password storage | bcrypt / argon2id | PCI-DSS guidance pushed industry circa 2010-2012 | Old hashes crackable in seconds with GPU |
| Manual `.env` parsing | python-dotenv | Library stable since 2013; v1.x API is frozen | No hand-rolled parser needed |

**Deprecated/outdated:**
- `configparser` for secrets: Still works but `.ini` format is less portable than `.env`; no community standard for secrets management with configparser.
- `hashlib.md5(password)`: Never acceptable for passwords. Not salted. GPU-crackable. Reject any suggestion of this pattern.

---

## Open Questions

1. **`os.getenv()` vs `os.environ[]` for required vars**
   - What we know: `os.getenv()` returns `None` silently; `os.environ["KEY"]` raises `KeyError` immediately
   - What's unclear: CONTEXT.md specified `os.getenv()` explicitly — this is the locked approach
   - Recommendation: Use `os.getenv()` as specified; add a startup assertion guard (`if not os.getenv("DB_PASSWORD"): raise EnvironmentError(...)`) to satisfy SEC-01's spirit without changing the locked API

2. **Python vs raw SQL for setup script**
   - What we know: Claude's Discretion area; Python script can hash the password inline; raw `.sql` file cannot
   - What's unclear: Nothing — bcrypt must run in Python
   - Recommendation: Use a Python script (`setup_users_table.py`). Raw SQL cannot bcrypt-hash the password. Python script is the only option that satisfies SEC-03 without a separate manual hashing step.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | None detected — no pytest.ini, no tests/ directory, no test scripts found |
| Config file | None — Wave 0 must create |
| Quick run command | `pytest tests/ -x -q` |
| Full suite command | `pytest tests/ -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SEC-01 | `db_marks.get_connection()` reads from env, not hardcoded string | unit | `pytest tests/test_sec01.py::test_credentials_from_env -x` | Wave 0 |
| SEC-01 | `db_connection.get_connection()` reads from env, not hardcoded string | unit | `pytest tests/test_sec01.py::test_db_connection_from_env -x` | Wave 0 |
| SEC-01 | No string "qCsfeuECc3MW" appears in any tracked .py file | smoke | `pytest tests/test_sec01.py::test_no_plaintext_password -x` | Wave 0 |
| SEC-02 | `.env` is listed in `.gitignore` | smoke | `pytest tests/test_sec02.py::test_env_in_gitignore -x` | Wave 0 |
| SEC-02 | `chat_memory.json` is listed in `.gitignore` | smoke | `pytest tests/test_sec02.py::test_chat_memory_in_gitignore -x` | Wave 0 |
| SEC-03 | `users` table exists with correct schema in MySQL | integration | manual-only (requires live DB) | N/A |
| SEC-03 | Seeded admin password_hash is valid bcrypt hash (starts with `$2b$`) | unit | `pytest tests/test_sec03.py::test_bcrypt_hash_format -x` | Wave 0 |
| SEC-03 | `bcrypt.checkpw()` verifies seed password against stored hash | unit | `pytest tests/test_sec03.py::test_bcrypt_round_trip -x` | Wave 0 |

**Note on SEC-03 DB integration test:** Requires a live MySQL instance. Mark with `@pytest.mark.integration` and skip by default in CI (`pytest -m "not integration"`).

### Sampling Rate

- **Per task commit:** `pytest tests/ -x -q -m "not integration"`
- **Per wave merge:** `pytest tests/ -v -m "not integration"`
- **Phase gate:** All non-integration tests green + manual verification of `users` table schema before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/__init__.py` — empty, makes tests a package
- [ ] `tests/test_sec01.py` — covers SEC-01: env-loading pattern, no plaintext credential check
- [ ] `tests/test_sec02.py` — covers SEC-02: .gitignore contents
- [ ] `tests/test_sec03.py` — covers SEC-03: bcrypt hash format and round-trip
- [ ] `pytest` install: add `pytest` to `requirements.txt` (dev dependency)

---

## Sources

### Primary (HIGH confidence)
- https://pypi.org/project/python-dotenv/ — version 1.2.2 confirmed, API usage verified (fetched 2026-03-11)
- https://pypi.org/project/bcrypt/ — version 5.0.0 confirmed, `hashpw`/`gensalt`/`checkpw` API verified (fetched 2026-03-11)
- `db_marks.py` (project source, read directly) — confirmed exact hardcoded credentials pattern at line 17-22
- `db_connection.py` (project source, read directly) — confirmed identical hardcoded pattern at line 3-9

### Secondary (MEDIUM confidence)
- MySQL 8.0 reference: `ENUM`, `BOOLEAN`, `AUTO_INCREMENT`, `INSERT IGNORE` — standard DDL, well-established
- Python `os` stdlib: `os.getenv()` / `os.environ` — standard library, no external verification needed

### Tertiary (LOW confidence)
- `.gitignore` template contents (IDE files, `__pycache__`, FAISS index files) — conventional patterns from community but not formally specified

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — both libraries verified on PyPI with current versions
- Architecture: HIGH — existing code read directly, patch pattern is straightforward
- Pitfalls: HIGH — bcrypt encoding requirement verified in official docs; other pitfalls derived from reading actual code
- Test map: MEDIUM — test commands are correct patterns but test files don't exist yet (Wave 0 gaps)

**Research date:** 2026-03-11
**Valid until:** 2026-04-11 (python-dotenv and bcrypt are stable libraries; 30 days is conservative)

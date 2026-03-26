# Phase 1: Secrets & Security Foundation - Context

**Gathered:** 2026-03-11
**Status:** Ready for planning

<domain>
## Phase Boundary

Remove hardcoded credentials from source code, protect sensitive files from git tracking, and create a `users` table with bcrypt-hashed passwords in MySQL. This is the security foundation that Phase 2 (authentication) builds on directly. No UI changes — pure infrastructure.

</domain>

<decisions>
## Implementation Decisions

### Environment Variable Naming
- DB vars named: `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`
- Only DB credentials added to `.env` this phase (OPENAI_API_KEY is already there)
- Create `.env.example` now with placeholder values — documents required vars for college IT

### Files to Fix
- Update `db_marks.py` and `db_connection.py` independently (not via shared module)
- Each file reads from `.env` using python-dotenv `load_dotenv()` + `os.getenv()`
- Prototype files (sql_executor.py, db_agent.py, test_mysql.py) — leave unchanged
- Add `python-dotenv` to `requirements.txt` with a pinned version

### users Table Schema
- Table goes in existing `kccitm` database
- Columns: `id` (AUTO_INCREMENT PK), `username` (VARCHAR UNIQUE NOT NULL), `password_hash` (VARCHAR NOT NULL), `role` (ENUM('admin','faculty') NOT NULL), `is_active` (BOOLEAN DEFAULT TRUE), `created_at` (TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
- Seed initial admin user from `.env` vars: `ADMIN_USER` and `ADMIN_PASS` (bcrypt-hash the password during seeding)
- Add `ADMIN_USER` and `ADMIN_PASS` to `.env.example`

### gitignore Scope
- Add `.env` and `chat_memory.json` (SEC-02 requirements)
- Also add standard Python ignores: `__pycache__/`, `*.pyc`, `*.pyo`, `.env`
- Claude's Discretion: what else to include (e.g., FAISS files, docker volumes, IDE files)

### Claude's Discretion
- bcrypt work factor / rounds (standard default is fine)
- Whether to use a Python setup script or raw SQL file for the users table creation
- Exact python-dotenv version pin
- Whether .env.example includes comments explaining each var

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `db_marks.get_connection()` (db_marks.py:16) — existing pattern to follow; just replace hardcoded values with `os.getenv()` calls
- `db_connection.get_connection()` (db_connection.py:3) — identical function, same fix

### Established Patterns
- MySQL connection via `mysql.connector.connect()` — keep using this, just swap credentials source
- No existing secrets management pattern — this phase establishes the pattern

### Integration Points
- `app.py` imports `db_marks` — fixing `db_marks.py` credentials directly fixes the production app path
- `db_connection.py` used only by prototype files but still contains the real password — fix it to eliminate exposure

</code_context>

<specifics>
## Specific Ideas

- The `.env` already exists at the project root with `OPENAI_API_KEY` — add DB vars to that same file
- The project is not yet a git repo, so no git history cleanup is needed — just ensure `.gitignore` is in place before `git init`
- ADMIN_USER / ADMIN_PASS seeding: the setup script should bcrypt-hash `ADMIN_PASS` before inserting — never store the plaintext from .env

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 01-secrets-security-foundation*
*Context gathered: 2026-03-11*

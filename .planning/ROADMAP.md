# Roadmap: KCCITM AI Assistant

## Overview

This roadmap transforms a working but insecure single-file prototype into a production-ready college deployment. The seven phases move in dependency order: secrets first (unblocks everything), then auth and sessions (user isolation), then DB hardening (stability under load), then architecture refactor (clean modules), then RAG skeleton (persistent knowledge store), then Docker deployment (on-prem packaging), then admin dashboard (operational visibility). Each phase delivers one coherent, verifiable capability.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Secrets & Security Foundation** - Move all credentials to .env; gitignore sensitive files; add bcrypt users table
- [ ] **Phase 2: Authentication & Session Isolation** - Login page, Redis-backed sessions, user management, per-user state
- [ ] **Phase 3: Database Hardening** - Connection pooling, bug fixes, error handling, input validation
- [ ] **Phase 4: Code Architecture Refactor** - Split app.py monolith into intent/, db/, rag/, ui/, session/ modules
- [ ] **Phase 5: RAG Skeleton** - Replace FAISS with ChromaDB persistence; wire fallback query path; ingestion script
- [ ] **Phase 6: Docker Deployment** - Dockerfile, docker-compose with all services, .env.example, README for college IT
- [ ] **Phase 7: Admin Dashboard** - Query logs, error monitoring, usage stats visible to admin role

## Phase Details

### Phase 1: Secrets & Security Foundation
**Goal**: No credentials exist in source code; sensitive files are excluded from version control; passwords are hashed
**Depends on**: Nothing (first phase)
**Requirements**: SEC-01, SEC-02, SEC-03
**Success Criteria** (what must be TRUE):
  1. Running `grep -r "password" .` returns no plaintext credentials in any tracked source file
  2. `.env` and `chat_memory.json` appear in `.gitignore` and are not tracked by git
  3. A `users` table exists in MySQL with a bcrypt-hashed password column (no plaintext stored)
  4. The application starts successfully reading DB credentials from environment variables only
**Plans:** 2/3 plans executed

Plans:
- [ ] 01-01-PLAN.md — Create test scaffolding (Wave 0): pytest package, SEC-01/02/03 test files
- [ ] 01-02-PLAN.md — Remove hardcoded credentials: .gitignore, .env.example, patch db_marks.py + db_connection.py
- [ ] 01-03-PLAN.md — Create users table: setup_users_table.py with bcrypt seeding + human verification

### Phase 2: Authentication & Session Isolation
**Goal**: Only authenticated users can access the app; each user's chat state is fully isolated in Redis
**Depends on**: Phase 1
**Requirements**: AUTH-01, AUTH-02, AUTH-03, AUTH-04, AUTH-05, SESS-01, SESS-02, SESS-03
**Success Criteria** (what must be TRUE):
  1. Visiting the app URL without a session redirects to a login page; no marks data is visible
  2. A valid faculty username/password combination grants access and persists across page refreshes
  3. Two users logged in simultaneously see only their own chat history and last-queried student — no bleed between sessions
  4. An admin user can create a new faculty account and deactivate an existing one via the UI
  5. Clicking logout clears the session immediately; the next page load returns to the login screen
**Plans**: TBD

### Phase 3: Database Hardening
**Goal**: The database layer is stable, efficient, and handles errors gracefully under concurrent load
**Depends on**: Phase 1
**Requirements**: DB-01, DB-02, DB-03, DB-04
**Success Criteria** (what must be TRUE):
  1. Under simultaneous queries from multiple browser tabs, no "too many connections" MySQL error appears
  2. A student with lab subjects shows a correct percentage based on actual max marks (not hardcoded 100)
  3. A name search or topper query that hits a MySQL error returns a clean error message in the chat — not an unhandled traceback
  4. Querying a malformed or non-existent roll number returns a user-facing message without hitting the database
**Plans**: TBD

### Phase 4: Code Architecture Refactor
**Goal**: app.py is split into focused modules; prototype files are no longer at the project root; dependencies are version-pinned
**Depends on**: Phase 2, Phase 3
**Requirements**: ARCH-01, ARCH-02, ARCH-03
**Success Criteria** (what must be TRUE):
  1. `app.py` is a thin entry point; intent detection, DB routing, RAG, UI rendering, and session logic each live in their own module directory
  2. All prototype files (main.py, marks_chatbot.py, sql_chatbot.py, etc.) are absent from the project root
  3. `requirements.txt` lists every package with an exact version pin; `pip install -r requirements.txt` produces a reproducible environment
  4. The app runs identically after refactor — all existing query types (roll number, name search, toppers, stats) return correct results
**Plans**: TBD

### Phase 5: RAG Skeleton
**Goal**: FAISS is replaced with a persistent ChromaDB store; the RAG path is wired as a live fallback; adding documents is possible without code changes
**Depends on**: Phase 4
**Requirements**: RAG-01, RAG-02, RAG-03, RAG-04
**Success Criteria** (what must be TRUE):
  1. After an app restart, the ChromaDB collection still contains previously ingested documents (no re-ingestion needed)
  2. A query that matches no student in the DB is routed to the RAG path and returns the closest knowledge base answer (or a clear "not found" message)
  3. Running the ingestion script with a new document adds it to the ChromaDB collection; subsequent queries can retrieve it
  4. FAISS and its index-rebuild startup cost are gone; cold start time is measurably shorter
**Plans**: TBD

### Phase 6: Docker Deployment
**Goal**: The full application stack (app, MySQL, ChromaDB, Redis) runs from a single docker-compose command on the college server
**Depends on**: Phase 5
**Requirements**: DEPL-01, DEPL-02, DEPL-03, DEPL-04
**Success Criteria** (what must be TRUE):
  1. `docker compose up` on a clean Linux VM starts all four services and the app is accessible in a browser with no manual setup steps
  2. `.env.example` documents every required environment variable; copying it to `.env` and filling in values is the only configuration needed
  3. MySQL data and ChromaDB vectors survive a `docker compose restart` without data loss
  4. College IT can follow the README to deploy on a new machine without asking the developer — instructions cover prerequisites, first-run setup, and common troubleshooting
**Plans**: TBD

### Phase 7: Admin Dashboard
**Goal**: Admins can see who is querying what, debug errors from production logs, and track usage patterns over time
**Depends on**: Phase 2, Phase 3
**Requirements**: ADMIN-01, ADMIN-02, ADMIN-03
**Success Criteria** (what must be TRUE):
  1. An admin account sees an "Admin" page in the sidebar; non-admin accounts do not see this page
  2. The admin page shows a table of recent queries with user, timestamp, query type, and query text — updated without restarting the app
  3. Errors from production (DB failures, intent mismatches) appear in an error log view with enough context to diagnose the issue
  4. A usage stats panel shows queries per day and a breakdown by query type (roll lookup, name search, topper, etc.)
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in dependency order: 1 → 2 → 3 → 4 → 5 → 6 (Phase 7 can run parallel to 6, after Phase 2+3)

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Secrets & Security Foundation | 2/3 | In Progress|  |
| 2. Authentication & Session Isolation | 0/? | Not started | - |
| 3. Database Hardening | 0/? | Not started | - |
| 4. Code Architecture Refactor | 0/? | Not started | - |
| 5. RAG Skeleton | 0/? | Not started | - |
| 6. Docker Deployment | 0/? | Not started | - |
| 7. Admin Dashboard | 0/? | Not started | - |

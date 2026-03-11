# Requirements: KCCITM AI Assistant

**Defined:** 2026-03-11
**Core Value:** Any faculty member can ask any academic question in plain English and get an instant, accurate answer.

## v1 Requirements

### Security

- [x] **SEC-01**: All DB passwords and secrets loaded from `.env` (no hardcoded credentials in source)
- [x] **SEC-02**: `.env` and `chat_memory.json` added to `.gitignore`
- [x] **SEC-03**: Passwords stored with bcrypt hashing in MySQL users table

### Authentication

- [ ] **AUTH-01**: Faculty/admin can log in with username + password via login page
- [ ] **AUTH-02**: Unauthenticated requests redirect to login page
- [ ] **AUTH-03**: Session persists across page navigation (Redis-backed)
- [ ] **AUTH-04**: Admin can create and deactivate faculty accounts via UI
- [ ] **AUTH-05**: Logout clears session from Redis

### Sessions

- [ ] **SESS-01**: Each logged-in user has fully isolated chat state (no shared file)
- [ ] **SESS-02**: Session stored in Redis, keyed by user
- [ ] **SESS-03**: `chat_memory.json` replaced — no single shared file

### Database

- [ ] **DB-01**: MySQL connection pooling (no fresh connection per query)
- [ ] **DB-02**: `calculate_percentage()` uses dynamic max marks from data (not hardcoded 100)
- [ ] **DB-03**: All DB functions have try/except error handling (name search, toppers, batch)
- [ ] **DB-04**: Roll number format validated before DB calls

### Code Architecture

- [ ] **ARCH-01**: `app.py` refactored into modules: `intent/`, `db/`, `rag/`, `ui/`, `session/`
- [ ] **ARCH-02**: Prototype files moved to `/prototypes/` subdirectory
- [ ] **ARCH-03**: `requirements.txt` all packages version-pinned

### RAG Skeleton

- [ ] **RAG-01**: FAISS in-memory code replaced with ChromaDB persistent vector store
- [ ] **RAG-02**: ChromaDB collection persists to disk (survives app restarts)
- [ ] **RAG-03**: RAG query path wired as fallback when DB finds no match
- [ ] **RAG-04**: Document ingestion script ready for adding college docs later

### Deployment

- [ ] **DEPL-01**: `Dockerfile` for the Streamlit app
- [ ] **DEPL-02**: `docker-compose.yml` with app + MySQL + ChromaDB + Redis services (external URL support for future 2-iMac split)
- [ ] **DEPL-03**: `.env.example` documenting all required environment variables
- [ ] **DEPL-04**: `README.md` with deployment instructions for college IT

### Admin Dashboard

- [ ] **ADMIN-01**: Admin page showing recent queries (user, timestamp, query type, query text)
- [ ] **ADMIN-02**: Error log view with context for debugging production issues
- [ ] **ADMIN-03**: Usage stats (queries per day, breakdown by query type)

## v2 Requirements

### Multi-Machine Deployment

- **INFRA-01**: Services split across 2 iMacs on local network (app+Redis on iMac 1, MySQL+ChromaDB on iMac 2)
- **INFRA-02**: Load balancing or failover between machines

### RAG Knowledge Base

- **RAG-05**: College exam rules and grading policy documents ingested
- **RAG-06**: Syllabus and subject content documents ingested
- **RAG-07**: Hostel and campus info documents ingested
- **RAG-08**: Career counseling content documents ingested

### Testing

- **TEST-01**: pytest suite for intent detection functions
- **TEST-02**: pytest suite for DB functions (get_marks, search, toppers)
- **TEST-03**: Auth and session integration tests

## Out of Scope

| Feature | Reason |
|---------|---------|
| Student-facing login | Faculty/admin tool only for v1 |
| Data entry / mutations | Read-only app; no grade submission |
| LLM API (OpenAI/Claude) | Deterministic intent parsing required for marks accuracy |
| OAuth / LDAP / SSO | No college LDAP available; simple auth sufficient |
| Mobile app | Web-first; Streamlit covers responsive basics |
| Full RAG content | Only skeleton in v1; content added in v2 |
| Automated tests | Deferred to v2 by user decision |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| SEC-01 | Phase 1 | Complete |
| SEC-02 | Phase 1 | Complete |
| SEC-03 | Phase 1 | Complete |
| AUTH-01 | Phase 2 | Pending |
| AUTH-02 | Phase 2 | Pending |
| AUTH-03 | Phase 2 | Pending |
| AUTH-04 | Phase 2 | Pending |
| AUTH-05 | Phase 2 | Pending |
| SESS-01 | Phase 2 | Pending |
| SESS-02 | Phase 2 | Pending |
| SESS-03 | Phase 2 | Pending |
| DB-01 | Phase 3 | Pending |
| DB-02 | Phase 3 | Pending |
| DB-03 | Phase 3 | Pending |
| DB-04 | Phase 3 | Pending |
| ARCH-01 | Phase 4 | Pending |
| ARCH-02 | Phase 4 | Pending |
| ARCH-03 | Phase 4 | Pending |
| RAG-01 | Phase 5 | Pending |
| RAG-02 | Phase 5 | Pending |
| RAG-03 | Phase 5 | Pending |
| RAG-04 | Phase 5 | Pending |
| DEPL-01 | Phase 6 | Pending |
| DEPL-02 | Phase 6 | Pending |
| DEPL-03 | Phase 6 | Pending |
| DEPL-04 | Phase 6 | Pending |
| ADMIN-01 | Phase 7 | Pending |
| ADMIN-02 | Phase 7 | Pending |
| ADMIN-03 | Phase 7 | Pending |

**Coverage:**
- v1 requirements: 27 total
- Mapped to phases: 27
- Unmapped: 0

---
*Requirements defined: 2026-03-11*
*Last updated: 2026-03-11 — Traceability confirmed against ROADMAP.md phases 1-7*

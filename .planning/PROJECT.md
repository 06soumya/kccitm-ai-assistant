# KCCITM AI Assistant

## What This Is

A Streamlit-based AI chatbot for KCCITM college that lets students and faculty query student academic records (marks, SGPA, toppers, averages) via natural language. It uses intent-based NL parsing over a MySQL database of student marks stored as JSON blobs, and will include a RAG knowledge base for college policy/syllabus queries.

## Core Value

Any student or faculty member can ask any academic question in plain English and get an instant, accurate answer — without knowing SQL or navigating a portal.

## Requirements

### Validated

<!-- Nothing shipped to production yet — brownfield existing prototype -->

- ✓ Roll number lookup → full marks display (student info, semesters, subjects DataFrames)
- ✓ Name fuzzy search with disambiguation for multiple matches
- ✓ Semester filtering (e.g., "sem 3 marks for 2104920100002")
- ✓ Subject alias resolution (100+ aliases for 21 subjects)
- ✓ Student stats: average marks, percentage, best/worst subject
- ✓ Batch-level toppers by CGPA
- ✓ Subject toppers (batch + semester + subject)
- ✓ Session state + chat_memory.json persistence (last_roll, query history)
- ✓ Sidebar with quick actions, past queries, example queries

### Active

<!-- Milestone v1.0: Production-Ready College Deployment -->

- [ ] All secrets moved to environment variables; no hardcoded credentials
- [ ] Role-based authentication (students see own data; faculty see all)
- [ ] Persistent ChromaDB vector store with real college knowledge base (50+ documents)
- [ ] Multi-user session isolation (no shared state corruption)
- [ ] MySQL connection pooling (eliminate per-query fresh connections)
- [ ] DB query optimization (batch/name scans use indexed queries or pre-filtered views)
- [ ] app.py refactored into modules: intent/, db/, rag/, ui/, session/
- [ ] Full pytest suite covering intent detection + DB functions
- [ ] All requirements.txt packages version-pinned
- [ ] Docker Compose: app + MySQL + ChromaDB services
- [ ] calculate_percentage() bug fixed (dynamic max marks per subject)
- [ ] Admin dashboard: query logs, error monitoring, user activity

### Out of Scope

- Real-time grade submission or data entry — this is read-only; mutations are out of scope
- Mobile app — web-first, Streamlit covers responsive basics
- OAuth/SSO with college LDAP — too infrastructure-dependent for v1; simple auth sufficient
- LLM-generated free-form answers (GPT/Claude API) — intent parsing stays rule-based to avoid hallucinations about marks

## Context

- **Database**: MySQL `kccitm.university_marks` table; roll_no + jsontext (JSON blob per student). ~600+ students per batch. No indexes on name fields — all name searches are full-table scans.
- **Data schema**: Student JSON has: name, rollno, enrollment, course, branch, fname, gender, result[] (semester objects with SGPA, total_marks_obt, marks[] array)
- **Batch year**: Derived from first 2 digits of roll_no (e.g., "21" → 2021)
- **RAG status**: FAISS index + SentenceTransformers loaded on startup (~438MB) but never queried; 4 hardcoded career documents with no semantic value
- **Security debt**: DB password hardcoded in db_marks.py, OpenAI key in .env (not gitignored), chat_memory.json with real student data committed to repo
- **Prototype files**: main.py, marks_chatbot.py, sql_chatbot.py, sql_executor.py, db_agent.py, db_connection.py — all unused by app.py

## Constraints

- **Tech stack**: Python + Streamlit — cannot change (college IT constraint)
- **Database**: MySQL on localhost — cannot migrate to cloud DB
- **Deployment target**: Single college server (Linux VM or Windows Server) — Docker Compose must work on-prem
- **Embedding model**: BAAI/bge-base-en-v1.5 already downloaded; avoid re-downloading large models
- **No LLM API**: No OpenAI/Claude API calls for query answers — intent parsing must be deterministic for marks accuracy
- **Concurrent users**: Expected 50-200 concurrent during exam result season; must not crash

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Keep Streamlit (not FastAPI + React) | College IT can manage simple Python app; Streamlit sufficient for chatbot UI | — Pending |
| ChromaDB over Pinecone/Weaviate | On-prem persistence, no API key, Python-native, free | — Pending |
| Simple username/password auth (not OAuth) | No LDAP integration available; college controls user list | — Pending |
| Keep intent-based parsing (not LLM routing) | Marks data accuracy is critical; regex/keyword patterns are deterministic | ✓ Good |
| MySQL JSON blob approach (not normalized tables) | Pre-existing data format; migration risk too high | — Pending |

---
*Last updated: 2026-03-11 — Milestone v1.0 started*

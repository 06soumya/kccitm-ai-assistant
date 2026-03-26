# External Integrations

**Analysis Date:** 2026-03-11

## APIs & External Services

**HuggingFace Model Hub:**
- `BAAI/bge-base-en-v1.5` - Sentence embedding model downloaded automatically by `sentence-transformers` on first run
  - SDK/Client: `sentence-transformers` pip package
  - Auth: None (public model, no API key required)
  - Usage: `app.py` line 147 — `SentenceTransformer("BAAI/bge-base-en-v1.5")` wrapped in `@st.cache_resource`
  - Network: Requires outbound internet on cold start to pull model weights from HuggingFace CDN

**Ollama (prototype only, not in production):**
- `phi3` local LLM - Used only in `main.py` prototype via `subprocess.run(["ollama", "run", "phi3"])`
  - SDK/Client: Ollama CLI binary (not a pip package; external system dependency)
  - Auth: None
  - Status: Not used by `app.py`; prototype only

## Data Storage

**Databases:**
- MySQL (primary and only database)
  - Provider: Self-hosted via Docker or local MySQL instance
  - Docker image: `mysql:latest` (defined in `docker-compose.yml`)
  - Host: `localhost`
  - Port: `3306`
  - Database name (production): `kccitm` — used by `db_marks.py` and `db_connection.py`
  - Database name (legacy prototype): `student_db` — used by `test_mysql.py`, `marks_chatbot.py`, `db_agent.py`
  - Client: `mysql-connector-python` (raw connector, no ORM)
  - Connection function: `db_marks.get_connection()` in `db_marks.py`
  - Table: `university_marks` — columns `roll_no` (indexed) and `jsontext` (JSON blob per student)
  - Credentials: Hardcoded in source (see Security note below)

**Vector Index:**
- FAISS `IndexFlatL2` - In-memory only, rebuilt at each application start
  - Library: `faiss-cpu`
  - Persistence: None — index is not saved to disk
  - Content: 4 hardcoded career counseling sentences in `app.py` `create_index()`
  - Dimension: Determined by `BAAI/bge-base-en-v1.5` output (768 dimensions)

**File Storage:**
- `chat_memory.json` — Local filesystem; written and read by `app.py` functions `save_persistent_memory()` and `load_persistent_memory()`
  - Contents: `last_roll` (last queried roll number) and `past_query_history` (up to 30 entries)
  - Location: Working directory where `streamlit run app.py` is executed

**Caching:**
- Streamlit `@st.cache_resource` — Caches the loaded `SentenceTransformer` model and FAISS index in process memory across reruns
  - Applied to: `load_model()` and `create_index()` in `app.py`
  - No external cache (no Redis, Memcached, etc.)

## Authentication & Identity

**Auth Provider:** None
- No user authentication, session tokens, or login system
- The Streamlit app is open to anyone who can reach the server

## Monitoring & Observability

**Error Tracking:** None
- Errors are caught with bare `except Exception as e` and surfaced as chat messages in the UI
- No Sentry, Datadog, or similar service

**Logs:**
- No structured logging; `print()` statements in prototype files only
- Streamlit prints tracebacks to stdout/stderr in development mode

## CI/CD & Deployment

**Hosting:** Not configured
- No deployment manifests beyond `docker-compose.yml` (which only covers MySQL, not the app itself)
- No Procfile, Dockerfile for the app, or cloud provider config detected

**CI Pipeline:** None detected

## Environment Configuration

**Required env vars:** None — all configuration is hardcoded
- MySQL credentials are embedded directly in source files:
  - `db_marks.py` — `host="localhost"`, `user="root"`, `database="kccitm"`
  - `db_connection.py` — identical credentials
  - `test_mysql.py`, `marks_chatbot.py`, `db_agent.py` — use a different legacy `student_db` database with different credentials

**Secrets location:** Embedded in Python source files (not in `.env` or secrets manager)

## Webhooks & Callbacks

**Incoming:** None

**Outgoing:** None

---

*Integration audit: 2026-03-11*

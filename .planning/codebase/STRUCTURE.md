# Codebase Structure

**Analysis Date:** 2026-03-11

## Directory Layout

```
rag-zip/                        # Project root
├── app.py                      # Production entry point — full Streamlit chatbot
├── db_marks.py                 # Database access layer — all MySQL queries
├── requirements.txt            # Python dependencies
├── chat_memory.json            # Persistent session state (auto-generated at runtime)
├── CLAUDE.md                   # Project instructions for Claude Code
├── FUNCTIONALITY.md            # Feature documentation
├── docker-compose.yml          # MySQL container configuration
├── mysql-custom.cnf/           # MySQL configuration directory
├── .env                        # Environment file (existence noted; contents not read)
├── .planning/
│   └── codebase/               # GSD analysis documents
├── __pycache__/                # Python bytecode cache (auto-generated)
│
│ — Prototype / Experimental Files —
├── main.py                     # CLI RAG demo using Ollama/phi3 locally
├── marks_chatbot.py            # Early CLI marks viewer (uses different DB credentials)
├── sql_chatbot.py              # Keyword-based SQL chatbot prototype
├── sql_executor.py             # Raw query runner utility (used by sql_chatbot.py)
├── db_agent.py                 # Prototype DB query runner (uses db_connection.py)
├── db_connection.py            # Duplicate connection helper (same creds as db_marks.py)
└── test_mysql.py               # Connectivity diagnostic script
```

## Directory Purposes

**Root (`/`):**
- Purpose: All source files are flat at root — no src/ subdirectory
- Key files: `app.py` (production), `db_marks.py` (database layer)

**`.planning/codebase/`:**
- Purpose: GSD codebase analysis documents
- Contains: Markdown files written by map-codebase agent
- Generated: By GSD tooling
- Committed: Yes

**`mysql-custom.cnf/`:**
- Purpose: MySQL server configuration override
- Contains: Custom MySQL settings for the Docker container
- Used by: `docker-compose.yml`

**`__pycache__/`:**
- Purpose: Python bytecode cache
- Generated: Yes, by Python interpreter
- Committed: No (should be in .gitignore)

## Key File Locations

**Entry Point:**
- `app.py`: The only production entry point. Run with `streamlit run app.py`.

**Database Layer:**
- `db_marks.py`: All MySQL connection and query logic. Single source of truth for data access.

**Configuration:**
- `requirements.txt`: Python package dependencies
- `docker-compose.yml`: MySQL container setup
- `mysql-custom.cnf/`: MySQL server config
- `.env`: Environment variables (not read by app code — credentials are hardcoded in `db_marks.py`)

**Persistence:**
- `chat_memory.json`: Auto-written at runtime by `save_persistent_memory()` in `app.py`. Stores `last_roll` and `past_query_history`. Not a source file — created on first run.

**Diagnostics:**
- `test_mysql.py`: Standalone connectivity test. Run directly with `python test_mysql.py`.

**Prototypes (not production):**
- `main.py`: Standalone CLI; requires local Ollama install with phi3 model
- `marks_chatbot.py`: CLI prototype; uses different DB credentials (`student_db`)
- `sql_chatbot.py`: Keyword chatbot prototype; depends on `sql_executor.py`
- `sql_executor.py`: Thin query runner; imports from `db_connection.py`
- `db_agent.py`: Another query runner prototype; imports from `db_connection.py`
- `db_connection.py`: Duplicate of connection logic already in `db_marks.py`

## Naming Conventions

**Files:**
- `snake_case.py` throughout (e.g., `db_marks.py`, `sql_chatbot.py`)
- `UPPER_CASE.md` for documentation (e.g., `CLAUDE.md`, `FUNCTIONALITY.md`)

**Functions:**
- `snake_case` for all functions (e.g., `get_marks`, `handle_db_query`, `extract_semester`)
- Boolean-returning intent checks are prefixed `is_` (e.g., `is_average_query`, `is_topper_query`, `is_name_search_query`)
- Extraction helpers prefixed `extract_` (e.g., `extract_semester`, `extract_batch`, `extract_name_candidate`)
- DB fetch functions prefixed `get_` (e.g., `get_marks`, `get_subject_toppers`)
- Calculation functions prefixed `calculate_` (e.g., `calculate_average_marks`, `calculate_percentage`)

**Variables:**
- `snake_case` for local variables and module-level constants
- `UPPER_SNAKE_CASE` for module-level constants (e.g., `SUBJECT_ALIASES`, `MEMORY_FILE`)
- Session state keys are `snake_case` strings (e.g., `"last_roll"`, `"pending_name_candidates"`)

**Result Dicts:**
- Always have a `"kind"` key with string value `"text"`, `"table"`, or `"marks_full"`

## Where to Add New Code

**New query intent (new type of question the bot can answer):**
- Add intent detection function in `app.py` following the `is_*` pattern
- Add extraction helpers in `app.py` following the `extract_*` pattern
- Add routing logic in `handle_db_query()` in `app.py`
- Add DB query function in `db_marks.py`
- Return a typed result dict from the handler

**New DB query / data retrieval:**
- Add to `db_marks.py` only — follow the `try/finally` connection pattern:
  ```python
  def my_new_query(param):
      connection = None
      cursor = None
      try:
          connection = get_connection()
          cursor = connection.cursor()
          # ... query logic ...
      finally:
          if cursor: cursor.close()
          if connection and connection.is_connected(): connection.close()
  ```
- Import the function in `app.py` at the top-level import block (lines 9–18)

**New subject alias:**
- Add to `SUBJECT_ALIASES` dict in `app.py` (lines 25–91)
- Key is the canonical name (must match DB exactly); value is a list of alias strings

**New result type (beyond text/table/marks_full):**
- Add a new `"kind"` value to the result dict pattern
- Add a matching branch in `render_single_message()` in `app.py`
- Add a matching branch in `process_query()` in `app.py`

**New session state key:**
- Add to `defaults` dict in `app.py` (lines 128–137) with a default value
- If it should persist across restarts, add to `save_persistent_memory()` and `load_persistent_memory()`

## Special Directories

**`.planning/`:**
- Purpose: GSD planning artifacts and codebase analysis
- Generated: By GSD commands
- Committed: Yes

**`mysql-custom.cnf/`:**
- Purpose: MySQL Docker configuration
- Generated: No
- Committed: Yes

---

*Structure analysis: 2026-03-11*

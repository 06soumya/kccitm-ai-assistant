# Architecture

**Analysis Date:** 2026-03-11

## Pattern Overview

**Overall:** Single-file Streamlit application with a dedicated database access layer. The pattern is a pipeline-based NL-to-query dispatcher: user input is classified by intent, routed to the appropriate DB query, and rendered through typed result objects.

**Key Characteristics:**
- All application logic lives in a single module (`app.py`) — UI, intent parsing, routing, session management, and rendering are co-located
- Database access is fully isolated in `db_marks.py`, which is the only file that connects to MySQL
- Results are passed as typed Python dicts (`kind: "text" | "table" | "marks_full"`) between the query handler and the chat renderer
- Streamlit session state serves as both in-memory state and the mechanism for multi-turn disambiguation
- Persistence is via a single flat JSON file (`chat_memory.json`) written on each turn

## Layers

**UI / Entry Layer:**
- Purpose: Renders chat messages, sidebar, and captures user input
- Location: `app.py` (lines 650–796)
- Contains: `render_single_message()`, `show_full_result()`, sidebar widget tree, `st.chat_input` handler
- Depends on: session state, result dicts from the query layer
- Used by: Streamlit runtime

**Intent Parsing Layer:**
- Purpose: Classifies raw user text into query intents and extracts structured parameters
- Location: `app.py` (lines 178–350)
- Contains: `detect_roll_number()`, `extract_semester()`, `extract_batch()`, `is_average_query()`, `is_percentage_query()`, `is_best_subject_query()`, `is_weakest_subject_query()`, `is_topper_query()`, `is_subject_topper_query()`, `is_batch_topper_query()`, `is_name_search_query()`, `extract_name_candidate()`, `replace_subject_aliases()`, `extract_subject_keywords()`
- Depends on: `SUBJECT_ALIASES` dict, standard `re` module
- Used by: `handle_db_query()`

**Query Routing Layer:**
- Purpose: Dispatches to the appropriate DB query path based on detected intent; manages multi-turn disambiguation state
- Location: `app.py` — `handle_db_query()` (lines 555–647), `execute_student_query()` (lines 436–552)
- Contains: `handle_db_query()` (top-level router), `execute_student_query()` (per-student sub-router), `resolve_pending_name_selection()` (disambiguation resolver)
- Depends on: intent parsing functions, `db_marks` module, session state
- Used by: `process_query()`

**Database Access Layer:**
- Purpose: All MySQL interaction and data transformation into Pandas DataFrames
- Location: `db_marks.py`
- Contains: `get_connection()`, `get_marks()`, `search_students_by_name()`, `get_subject_toppers()`, `get_batch_toppers_by_cgpa()`, `calculate_average_marks()`, `calculate_percentage()`, `get_best_or_weakest_subject()`, `derive_batch_from_roll()`
- Depends on: `mysql.connector`, `pandas`, `json`, `re`
- Used by: `app.py` query routing layer exclusively

**RAG Layer (minimal):**
- Purpose: Embeds a small static career counseling knowledge base; currently unused in query routing
- Location: `app.py` (lines 145–173)
- Contains: `load_model()` (cached `SentenceTransformer`), `create_index()` (cached FAISS index over 4 hardcoded documents)
- Depends on: `sentence_transformers`, `faiss`
- Used by: Not called during query processing — present but disconnected from the main pipeline

## Data Flow

**Roll Number Query:**

1. `st.chat_input` captures query string
2. `process_query()` appends user message to `st.session_state.current_chat` and calls `handle_db_query()`
3. `handle_db_query()` calls `detect_roll_number()` → finds a 6+ digit sequence
4. Routes to `execute_student_query()` with the roll number
5. `fetch_and_cache_student()` calls `db_marks.get_marks(roll_no)` → returns `(student_df, semester_df, subject_df)`
6. Cached into `st.session_state` (last_roll, last_student_df, last_semester_df, last_subject_df) and persisted to `chat_memory.json`
7. `execute_student_query()` applies optional semester filter, subject match, or stat calculation
8. Returns a typed result dict (`kind: "marks_full"`, `kind: "table"`, or `kind: "text"`)
9. `process_query()` appends result to `st.session_state.current_chat`
10. Streamlit reruns; `render_single_message()` renders each message

**Name Search with Disambiguation:**

1. `is_name_search_query()` returns True
2. `extract_name_candidate()` strips noise words to get a name fragment
3. `db_marks.search_students_by_name()` does full table scan, scores by exact/contains/token match
4. If multiple results: stored in `st.session_state.pending_name_candidates`; user prompted to select
5. Next user turn: `resolve_pending_name_selection()` matches by sequence number or exact name
6. Selected roll forwarded to `execute_student_query()`

**Topper Queries:**

1. `is_subject_topper_query()` or `is_batch_topper_query()` fires
2. `extract_batch()` and `extract_semester()` pull parameters
3. Routes to `db_marks.get_subject_toppers()` or `db_marks.get_batch_toppers_by_cgpa()`
4. Returns ranked DataFrame as `kind: "table"` result

**State Management:**
- In-memory: `st.session_state` dict with keys `last_roll`, `last_student_df`, `last_semester_df`, `last_subject_df`, `pending_name_candidates`, `pending_name_query`, `past_query_history`, `current_chat`
- Persistent across restarts: `chat_memory.json` stores `last_roll` and `past_query_history` (last 30 entries)
- On startup: persistent memory is loaded and merged into session state defaults; if `last_roll` is set but DataFrames are missing, `fetch_and_cache_student()` is called to restore them

## Key Abstractions

**Typed Result Dict:**
- Purpose: Decouples query logic from rendering — every query handler returns one of three shapes
- Pattern:
  ```python
  {"kind": "text", "content": "..."}
  {"kind": "table", "title": "...", "df": pd.DataFrame}
  {"kind": "marks_full", "roll": "...", "student_df": ..., "semester_df": ..., "subject_df": ...}
  ```
- Consumed by: `process_query()` → `append_current_chat()` → `render_single_message()`

**SUBJECT_ALIASES:**
- Purpose: Maps informal names and subject codes to canonical subject names used in DB matching
- Location: `app.py` lines 25–91
- Pattern: `canonical_name: [list_of_aliases]` — `replace_subject_aliases()` iterates and regex-substitutes

**Batch Derivation:**
- Purpose: Derives enrollment year from roll number prefix (first 2 digits)
- Location: `db_marks.derive_batch_from_roll()` — e.g., roll `2104920100002` → batch `2021`
- Used by: topper queries, name search filtering, sidebar display

**Three-DataFrame Student Record:**
- Purpose: Normalized view of a student's JSON blob
- Location: `db_marks.get_marks()` returns `(student_df, semester_df, subject_df)`
  - `student_df`: two-column `Field/Value` table of personal info
  - `semester_df`: one row per semester with SGPA, totals, result status
  - `subject_df`: one row per subject per semester with internal/external/grade

## Entry Points

**Streamlit App (`app.py`):**
- Location: `app.py`
- Triggers: `streamlit run app.py`
- Responsibilities: Initializes session state and persistent memory, loads embedding model and FAISS index, renders sidebar and chat history, receives `st.chat_input`, calls `process_query()`, triggers `st.rerun()` after each message

## Error Handling

**Strategy:** Broad exception catching at the query execution boundary; errors surface as `kind: "text"` chat messages.

**Patterns:**
- `process_query()` wraps `handle_db_query()` in `try/except Exception as e` → appends error text to chat
- `db_marks.get_marks()` catches all exceptions and returns `pd.DataFrame([{"Field": "Error", "Value": str(e)}])` — callers check for this sentinel shape
- `db_marks` functions use `try/finally` to guarantee cursor and connection close even on failure
- `load_persistent_memory()` catches JSON parse errors and returns a clean default dict
- `safe_float()` in `db_marks.py` silently returns `None` for unparseable values

## Cross-Cutting Concerns

**Logging:** None — errors are surfaced to the UI as chat messages; no file/structured logging
**Validation:** Intent detection functions return booleans based on regex/keyword presence; no schema validation on DB data
**Authentication:** None — the app is open-access; MySQL credentials are hardcoded in `db_marks.get_connection()`

---

*Architecture analysis: 2026-03-11*

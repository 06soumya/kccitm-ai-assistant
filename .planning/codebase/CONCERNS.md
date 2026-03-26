# Codebase Concerns

**Analysis Date:** 2026-03-11

## Tech Debt

**No dependency version pinning:**
- Issue: `requirements.txt` lists all packages without version constraints (e.g., `streamlit`, `sentence-transformers`, `faiss-cpu`, `mysql-connector-python`, `pandas`)
- Files: `requirements.txt`
- Impact: Upgrades to any package can silently break the application; no reproducible installs
- Fix approach: Pin all dependencies to specific versions (e.g., `streamlit==1.32.0`) after testing

**Monolithic `app.py` with mixed concerns:**
- Issue: `app.py` (795 lines) contains intent detection, routing, data transformation, session management, persistent memory, UI rendering, and business logic all in a single file with no module separation
- Files: `app.py`
- Impact: Difficult to test, debug, or extend any individual piece without reading the whole file; changes to intent logic risk breaking rendering
- Fix approach: Split into `intent.py`, `query_handler.py`, `session.py`, `render.py` modules

**Phantom prototype files in production repo:**
- Issue: `main.py`, `marks_chatbot.py`, `sql_chatbot.py`, `sql_executor.py`, `db_connection.py`, `db_agent.py` are all dead prototypes that are not imported by `app.py` but sit at the root alongside production code
- Files: `main.py`, `marks_chatbot.py`, `sql_chatbot.py`, `sql_executor.py`, `db_connection.py`, `db_agent.py`
- Impact: Misleading to maintainers; prototype files contain conflicting credentials and outdated database names (`student_db` vs `kccitm`)
- Fix approach: Move to a `prototypes/` or `archive/` subdirectory or delete entirely

**No connection pooling:**
- Issue: Every DB function in `db_marks.py` opens a new MySQL connection on every call and closes it after. Functions like `search_students_by_name` fetch all rows and process in Python.
- Files: `db_marks.py` — `get_marks()` (line 46), `search_students_by_name()` (line 132), `get_subject_toppers()` (line 275), `get_batch_toppers_by_cgpa()` (line 367)
- Impact: High connection overhead on every query; no connection reuse between Streamlit reruns
- Fix approach: Use a persistent connection pool (e.g., `mysql.connector.pooling.MySQLConnectionPool`) or SQLAlchemy with pooling

**Derived CGPA is not official CGPA:**
- Issue: `get_batch_toppers_by_cgpa()` computes CGPA as a simple mean of all available SGPAs and labels the column "Derived CGPA" — this is a non-standard approximation and differs from university-calculated CGPA
- Files: `db_marks.py` line 409, `app.py` line 611
- Impact: Rankings presented to users may not match actual university rankings; misleading for academic purposes
- Fix approach: Source CGPA directly from the stored JSON if the field exists, or display a clear disclaimer that it is an approximation

**`calculate_percentage()` assumes 100 marks maximum per subject:**
- Issue: `max_total = len(temp) * 100` hardcodes 100 as the maximum per subject regardless of the actual maximum marks
- Files: `db_marks.py` line 267
- Impact: Percentage values are systematically wrong for subjects with maximum marks other than 100 (e.g., labs, practical subjects)
- Fix approach: Source the maximum marks from subject data if available, or document the limitation

**`chat_memory.json` committed to repo with real student data:**
- Issue: `chat_memory.json` contains a real roll number (`2204920100100`) and actual query history in plaintext at the project root
- Files: `chat_memory.json`
- Impact: Student PII stored in the repository; if committed to version control, becomes permanent history
- Fix approach: Add `chat_memory.json` to `.gitignore`; store in a user-specific or OS temp directory instead

## Known Bugs

**`search_students_by_name` has no error handling for missing cursor:**
- Symptoms: If the MySQL connection fails, the `finally` block tries to close `cursor` which may still be `None`, but this is handled. However, if the connection succeeds but the `execute` fails, there is no `except` block — the exception propagates uncaught from `search_students_by_name`, unlike `get_marks` which has a full `try/except/finally`
- Files: `db_marks.py` lines 132-214
- Trigger: Any MySQL error during `search_students_by_name` execution
- Workaround: The `process_query()` catch-all in `app.py` line 708 will surface the error to the user

**`get_subject_toppers` and `get_batch_toppers_by_cgpa` have no except block:**
- Symptoms: Database errors during topper queries propagate as unhandled exceptions
- Files: `db_marks.py` lines 275-364, 367-429
- Trigger: MySQL connectivity issues or malformed JSON in any row
- Workaround: The `process_query()` catch-all in `app.py` line 708 will surface the error to the user

**`db_agent.py` never closes its database connection:**
- Symptoms: Connection leak on every call — `run_query()` opens a connection, creates a cursor, but the `finally` block is absent; the connection is never explicitly closed
- Files: `db_agent.py` lines 3-18
- Trigger: Any call to `run_query()` in `db_agent.py`
- Workaround: File is not used by production `app.py`

**`marks_chatbot.py` uses a module-level cursor that is never closed:**
- Symptoms: `cursor = db.cursor()` at module level (line 7), never closed; database connection held open for the entire process lifetime
- Files: `marks_chatbot.py` lines 5-7
- Trigger: Running `marks_chatbot.py`
- Workaround: File is not used by production `app.py`

**`extract_batch()` will misidentify years in non-batch contexts:**
- Symptoms: The fallback `r"\b(20\d{2})\b"` in `extract_batch()` will match any 4-digit year in a query (e.g., "math 2024 exam" extracts batch 2024 from "2024")
- Files: `app.py` lines 216-221
- Trigger: Queries that mention years in contexts other than batch specification
- Workaround: More specific query patterns are checked first; fallback only fires if no explicit "batch" keyword is found

**`is_topper_query` matches on keyword "top" alone:**
- Symptoms: `is_topper_query()` returns `True` for any query containing "top" (e.g., "top marks", "topic", "topology") because it checks `"top" in t`
- Files: `app.py` lines 244-246
- Trigger: Queries about "top marks for..." or subject names containing "top"
- Workaround: Subsequent `is_subject_topper_query` and `is_batch_topper_query` checks require batch/semester, reducing false positives somewhat

## Security Considerations

**Hardcoded MySQL credentials in source code:**
- Risk: Database password is in plaintext in source files committed to the repository
- Files: `db_marks.py` line 20 (`password="qCsfeuECc3MW"`), `db_connection.py` line 7 (different password: `password="qCsfeuECc3MW"`), `marks_chatbot.py` line 7 (another password: `password="jh11d7700"`), `test_mysql.py` line 7 (same: `password="jh11d7700"`), `db_agent.py` line 7 (same: `password="jh11d7700"`)
- Current mitigation: None — credentials are in plaintext
- Recommendations: Move credentials to environment variables; use `python-dotenv` to load from `.env`; add `.env` and any credential files to `.gitignore`

**Student PII exposed in memory file:**
- Risk: `chat_memory.json` stores student roll numbers and query history; if the repo is made public or the file is committed, student data leaks
- Files: `chat_memory.json`
- Current mitigation: None
- Recommendations: Add `chat_memory.json` to `.gitignore`; store in OS temp or user home directory; consider encryption at rest

**No input sanitization on roll number queries:**
- Risk: `detect_roll_number()` returns raw regex-extracted strings passed directly to a parameterized query — this specific path is safe. However, `subject_query` in `get_subject_toppers()` is used in Python-side string matching, not SQL, so SQL injection is not a direct risk. The broader concern is that no validation ensures roll numbers are of the expected format before querying.
- Files: `app.py` lines 178-180, `db_marks.py` lines 285-293
- Current mitigation: Parameterized queries used in `db_marks.py` — SQL injection is not present
- Recommendations: Validate roll number format (length, digit-only) before making DB calls to prevent unnecessary queries

**`sql_executor.py` executes raw arbitrary SQL strings:**
- Risk: `run_query()` in `sql_executor.py` executes whatever string is passed — while `sql_chatbot.py` only passes hardcoded strings, the function itself accepts arbitrary SQL
- Files: `sql_executor.py` lines 3-14, `sql_chatbot.py` lines 1-35
- Current mitigation: Not exposed through the production Streamlit app
- Recommendations: Delete or archive these prototype files to prevent future misuse

## Performance Bottlenecks

**Full table scan on every name search:**
- Problem: `search_students_by_name()` executes `SELECT roll_no, jsontext FROM university_marks` with no WHERE clause, fetching the entire table and processing all rows in Python
- Files: `db_marks.py` lines 140-145
- Cause: JSON data is stored as a blob; student names are not indexed as a column
- Improvement path: Add a `name` column to `university_marks` populated from the JSON on insert; add a fulltext index; or use MySQL's `JSON_EXTRACT` with a generated column

**Full table scan for batch-level topper queries:**
- Problem: `get_subject_toppers()` and `get_batch_toppers_by_cgpa()` filter by `CAST(roll_no AS CHAR) LIKE %s` — casting a numeric column to char on every row prevents index use
- Files: `db_marks.py` lines 285-293, 377-385
- Cause: `roll_no` is stored as a numeric type but batch prefix is extracted from its string representation
- Improvement path: Store `roll_no` as VARCHAR, or add a `batch_year` column with an index

**FAISS vector index built from 4 documents:**
- Problem: The RAG knowledge base in `app.py` has exactly 4 hardcoded documents and a FAISS index built from them. This is functionally equivalent to a simple list comparison and wastes the embedding model load time.
- Files: `app.py` lines 155-170
- Cause: The knowledge base was never populated beyond the initial demo content
- Improvement path: Either populate with real career counseling content or remove FAISS/embedding components entirely; they are not used for any student marks queries

**Embedding model loaded on every cold start:**
- Problem: `SentenceTransformer("BAAI/bge-base-en-v1.5")` is loaded via `@st.cache_resource` — correct for Streamlit, but the model is ~438MB and adds significant cold start time
- Files: `app.py` lines 145-150
- Cause: Required by FAISS RAG component which is currently unused for actual query handling
- Improvement path: Remove if the RAG component is not going to be expanded; model load cost is wasted for current functionality

## Fragile Areas

**Intent detection via keyword matching is brittle:**
- Files: `app.py` lines 224-261 (`is_average_query`, `is_percentage_query`, `is_best_subject_query`, `is_weakest_subject_query`, `is_topper_query`, `is_subject_topper_query`, `is_batch_topper_query`, `is_name_search_query`)
- Why fragile: Overlapping keyword sets with no priority or conflict resolution; order of `if` checks in `handle_db_query()` and `execute_student_query()` determines behavior; adding new query types requires auditing all existing patterns for conflicts
- Safe modification: Add new intent checks before the final `execute_student_query()` fallback; document any keyword that overlaps with existing patterns
- Test coverage: None — no automated tests for any intent detection logic

**`pending_name_candidates` state management:**
- Files: `app.py` lines 414-433, 556-563
- Why fragile: The disambiguation flow relies on `st.session_state.pending_name_candidates` being cleared exactly once after a selection. If the user sends an ambiguous follow-up message that is not recognized as a selection, the state is never cleared, and all future queries go through `resolve_pending_name_selection()` first. There is no timeout or cancel mechanism.
- Safe modification: Add an explicit "cancel" keyword detection in `resolve_pending_name_selection()`; always clear the pending state when a new roll number is detected in the query
- Test coverage: None

**`SUBJECT_ALIASES` dict is the only mapping for subject recognition:**
- Files: `app.py` lines 25-91
- Why fragile: Subject names and codes are hardcoded; adding new semesters/subjects requires manual dict updates; typos in aliases silently fail to match
- Safe modification: Add new subjects with at least 3 aliases (code, short name, full name); test with `replace_subject_aliases()` directly
- Test coverage: None

## Scaling Limits

**In-memory session state for multi-user:**
- Current capacity: Streamlit session state is per-browser-session, which is correct behavior; however, `chat_memory.json` is a single shared file at the process level
- Limit: If multiple users run the app simultaneously (e.g., multiple browser tabs with a shared Streamlit server), the `chat_memory.json` will be overwritten by whichever session saves last, corrupting other sessions' history
- Scaling path: Use per-user session IDs for memory files, or replace file-based persistence with a proper session store (Redis, database)

**`search_students_by_name` loads entire dataset into memory:**
- Current capacity: Works for small datasets; becomes a memory and latency problem as the `university_marks` table grows
- Limit: With tens of thousands of students, the full fetch and Python-side filtering will be slow
- Scaling path: Push filtering to MySQL using `JSON_EXTRACT` or a denormalized `name` column with a FULLTEXT index

## Dependencies at Risk

**No version constraints on any dependency:**
- Risk: All five dependencies in `requirements.txt` are unpinned; breaking changes in any package will affect the application on next `pip install`
- Impact: `sentence-transformers` and `faiss-cpu` have been known to have breaking API changes between minor versions
- Migration plan: Run `pip freeze > requirements-lock.txt` and use the locked file for deployments; update `requirements.txt` to specify minimum compatible versions

**`faiss-cpu` loaded but underutilized:**
- Risk: `faiss-cpu` is a platform-specific compiled binary with occasional install failures on non-standard platforms; it is loaded for a 4-document knowledge base
- Impact: If FAISS fails to install, the entire app fails to start
- Migration plan: Replace the 4-document RAG with a simple list similarity check, removing the FAISS dependency; or expand the knowledge base to justify the dependency

## Missing Critical Features

**No authentication or authorization:**
- Problem: Any user with access to the Streamlit URL can query any student's academic records by roll number or name
- Blocks: Production deployment to any externally accessible URL is a privacy risk without login gates

**No tests of any kind:**
- Problem: There are no unit tests, integration tests, or end-to-end tests in the codebase
- Blocks: Any refactoring is risky; intent detection bugs can only be caught manually; no CI pipeline is possible

## Test Coverage Gaps

**All intent detection logic is untested:**
- What's not tested: `is_average_query`, `is_percentage_query`, `is_best_subject_query`, `is_weakest_subject_query`, `is_topper_query`, `is_subject_topper_query`, `is_batch_topper_query`, `is_name_search_query`, `extract_batch`, `extract_semester`, `extract_name_candidate`, `replace_subject_aliases`
- Files: `app.py` lines 178-350
- Risk: Keyword pattern changes silently break query routing for all existing query types
- Priority: High

**All database functions are untested:**
- What's not tested: `get_marks`, `search_students_by_name`, `get_subject_toppers`, `get_batch_toppers_by_cgpa`, `calculate_average_marks`, `calculate_percentage`, `get_best_or_weakest_subject`
- Files: `db_marks.py`
- Risk: JSON schema changes in stored data silently produce empty or incorrect results
- Priority: High

---

*Concerns audit: 2026-03-11*

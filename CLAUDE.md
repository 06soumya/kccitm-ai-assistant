# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Run the main application:**
```
streamlit run app.py
```

**Install dependencies:**
```
pip install -r requirements.txt
```

**Test database connectivity:**
```
python test_mysql.py
```

## Architecture

This is the **KCCITM AI Assistant** — a Streamlit chatbot for querying student academic records from a MySQL database. It uses RAG (FAISS + SentenceTransformers) for a small career counseling knowledge base, but the primary function is structured NL-to-query intent parsing over student marks data.

### Production App: `app.py`

The main file. Query handling pipeline:
1. **Intent detection** — regex/keyword patterns classify the query as one of: roll number lookup, name search, topper query (subject-level or batch-level), or follow-up on a previous student
2. **`handle_db_query()`** routes to the appropriate path
3. **`execute_student_query()`** handles per-student queries (marks, averages, percentage, best/weakest subject, semester filter)
4. Results are typed dicts (`kind: "text" | "table" | "marks_full"`) and stored in `st.session_state.current_chat` for rendering

**Session state** tracks: `last_roll`, `last_student_df/semester_df/subject_df`, `pending_name_candidates` (for disambiguation when multiple students match a name search), and `past_query_history`. Persistent memory is saved to `chat_memory.json`.

**Subject aliases** (`SUBJECT_ALIASES` dict) map informal names and subject codes to canonical names used for DB matching (e.g., "pps", "kcs101t" → "programming for problem solving").

### Database Layer: `db_marks.py`

Single source of truth for all DB queries. Connects to MySQL `student_db`, table `university_marks` (columns: `roll_no`, `jsontext`). The `jsontext` column stores a JSON blob per student with fields: `name`, `rollno`, `enrollment`, `course`, `branch`, `fname`, `gender`, and `result` (array of semester objects, each with `semester`, `session`, `result_status`, `total_marks_obt`, `SGPA`, and `marks` array).

Key functions:
- `get_marks(roll_no)` → returns `(student_df, semester_df, subject_df)` parsed from JSON
- `search_students_by_name(name_query, batch_year)` → scans all rows, scores by exact/contains/token match
- `get_subject_toppers(batch_year, semester_no, subject_query)` → filters batch by roll prefix, finds top scorers
- `get_batch_toppers_by_cgpa(batch_year)` → derives CGPA as mean of all SGPAs, ranks batch

Batch year is derived from the first 2 digits of the roll number (e.g., roll `2104920100002` → batch 2021).

### Other Files

- `app.py` is the only production entry point — the other files are prototypes/experiments:
  - `main.py` — CLI RAG demo using Ollama/phi3 locally
  - `marks_chatbot.py` — early CLI marks viewer
  - `sql_chatbot.py` — keyword-based SQL chatbot prototype
  - `sql_executor.py` + `db_connection.py` + `db_agent.py` — utility/prototype DB helpers

### Database Configuration

MySQL credentials are hardcoded in `db_marks.py` (and duplicated in prototype files). The active connection used by `app.py` is in `db_marks.get_connection()`:
- host: `localhost`, user: `root`, database: `student_db`
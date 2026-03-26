# Coding Conventions

**Analysis Date:** 2026-03-11

## Naming Patterns

**Files:**
- `snake_case` for all Python files: `db_marks.py`, `sql_executor.py`, `db_connection.py`
- `app.py` as the single production entry point
- Prototype files use descriptive snake_case: `marks_chatbot.py`, `sql_chatbot.py`

**Functions:**
- `snake_case` throughout all files
- Verb-noun pattern for action functions: `get_marks()`, `search_students_by_name()`, `calculate_average_marks()`, `fetch_and_cache_student()`
- `is_` prefix for boolean predicate functions: `is_average_query()`, `is_percentage_query()`, `is_best_subject_query()`, `is_topper_query()`, `is_name_search_query()`
- `extract_` prefix for parsing/extraction functions: `extract_semester()`, `extract_batch()`, `extract_name_candidate()`, `extract_subject_keywords()`
- `get_` prefix for data retrieval: `get_marks()`, `get_connection()`, `get_subject_toppers()`, `get_batch_toppers_by_cgpa()`
- `calculate_` prefix for computation: `calculate_average_marks()`, `calculate_percentage()`, `calculate_subject_total()`

**Variables:**
- `snake_case` for local variables: `batch_year`, `semester_no`, `roll_no`, `subject_query`
- Short aliases for temporaries: `t = str(text).lower()`, `q = normalize_text(query)`
- `_df` suffix for DataFrame variables: `student_df`, `semester_df`, `subject_df`, `candidates_df`, `best_df`
- `_rows` suffix for list-of-dict accumulators before DataFrame construction: `semester_rows`, `subject_rows`, `topper_rows`, `result_rows`

**Constants:**
- `SCREAMING_SNAKE_CASE`: `MEMORY_FILE`, `SUBJECT_ALIASES`

**DataFrame Columns:**
- Title Case for all column names: `"Subject Name"`, `"Roll Number"`, `"Match Score"`, `"Result Status"`, `"Total Marks"`, `"Derived CGPA"`

## Code Style

**Formatting:**
- No formatter config detected (no `.prettierrc`, `pyproject.toml` with black, `.flake8`, etc.)
- Consistent 4-space indentation throughout
- Blank lines used generously between logical blocks within functions
- Section separator comments used in `app.py`: `# -------------------------------------------------`

**Linting:**
- No linting config detected
- No type annotations used anywhere in the codebase

## Import Organization

**Order in `app.py`:**
1. Standard library: `re`, `json`, `os`
2. Third-party: `streamlit`, `pandas`, `sentence_transformers`, `faiss`
3. Local: `from db_marks import (...)`

**Order in `db_marks.py`:**
1. Third-party: `mysql.connector`, `pandas`, `json`, `re`

**Path Aliases:**
- None used; local imports are flat `from db_marks import ...`

## Error Handling

**Patterns:**

In `db_marks.py` — DB functions use try/except/finally:
```python
def get_marks(roll_no):
    connection = None
    cursor = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        # ...
    except Exception as e:
        error_df = pd.DataFrame([{"Field": "Error", "Value": str(e)}])
        return error_df, None, None
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()
```

In `app.py` — top-level query dispatch catches broadly:
```python
try:
    result = handle_db_query(query)
    # ...
except Exception as e:
    append_current_chat("assistant", "text", f"❌ The database query could not be processed: {e}")
```

In `db_marks.py` — inner JSON parse loops swallow errors silently:
```python
for roll_no, raw_json in rows:
    try:
        data = json.loads(raw_json)
    except Exception:
        continue
```

Errors from DB layer are returned as sentinel DataFrames (a DataFrame with a single `"Error"` row), not raised exceptions. Callers must check for this pattern explicitly.

**Null guards:** Functions consistently guard against `None` and empty DataFrames at entry:
```python
if subject_df is None or subject_df.empty:
    return None
```

## Logging

**Framework:** None. No logging library used.

**Patterns:**
- Prototype files use `print()` for output
- Production `app.py` uses `st.markdown()` and chat message rendering for user-visible output
- Errors surfaced to the user as emoji-prefixed strings: `"❌ No record found for this student."`
- No structured logging, no log levels, no file-based logs

## Comments

**When to Comment:**
- Section dividers used in `app.py` to mark logical blocks: `# PERSISTENT MEMORY`, `# SESSION STATE`, `# HELPERS`, `# SIDEBAR`
- Inline comments are absent in `db_marks.py`
- No docstrings on any function in the codebase

**JSDoc/TSDoc:**
- Not applicable (Python). No docstrings used.

## Function Design

**Size:**
- `execute_student_query()` in `app.py` (lines 436-552) is the longest at ~116 lines — handles all per-student query dispatch
- `handle_db_query()` in `app.py` (~93 lines) routes between topper, name, and roll queries
- Helper functions in `db_marks.py` are concise (10–40 lines each)

**Parameters:**
- Functions accept simple scalar types (`str`, `int`) or DataFrames
- Optional parameters use `None` as default: `forced_roll=None`, `batch_year=None`, `top_n=50`
- No use of `*args` or `**kwargs`

**Return Values:**
- DB functions return tuples of DataFrames: `(student_df, semester_df, subject_df)`
- Query handler functions return typed dicts with a `"kind"` discriminator field:
  ```python
  {"kind": "text", "content": "..."}
  {"kind": "table", "title": "...", "df": df}
  {"kind": "marks_full", "roll": ..., "student_df": ..., "semester_df": ..., "subject_df": ...}
  ```
- Boolean predicates return `True`/`False`
- Extraction/parsing functions return `None` on failure (not exceptions)

## Module Design

**Exports:**
- No `__all__` defined in any module
- `db_marks.py` functions are imported explicitly by name in `app.py`

**Barrel Files:**
- None used

## Text Normalization Pattern

A recurring pattern throughout both files: lowercase and strip before comparison:
```python
t = str(text).lower().strip()
```
`normalize_text()` strips non-alphanumeric characters; `normalize_name()` uppercases and collapses whitespace. Both exist in `app.py` and a version of `normalize_name()` is duplicated in `db_marks.py`.

---

*Convention analysis: 2026-03-11*

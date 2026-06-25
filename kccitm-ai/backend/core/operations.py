"""
Deterministic executor functions for known query operations.

Replaces LLM-generated SQL on common query shapes. The planner still extracts
intent + slots, and the formatter LLM still writes the prose response, but the
SQL in between is parameterized code — not generated text.

Flow:
    planner (LLM)
        │  operation, slots
        ▼
    dispatch()           ← this module
        │
        ├── matched ──▶ executor function ──▶ DB ──▶ ExecutorResult
        │
        └── no match ─▶ None  (orchestrator falls through to LLM-SQL path)

Why this exists:
    LLM-generated SQL fails on shapes the model has seen variants of but
    doesn't have firm patterns for — subqueries scoped wrong, hallucinated
    columns, retries that compound latency. For queries that fit a known
    shape (top_students, pass_rate, lookups, simple aggregates) executing
    deterministic SQL is faster, correct by construction, and cacheable on
    (operation, slots) instead of query text.

The LLM-SQL pipeline remains as a long-tail safety valve for novel queries
that don't match any registered operation.

Adding a new operation:
    1. Write an async function with the (slots) → ExecutorResult signature
    2. Add a dispatch rule in dispatch() that maps planner output to it
    3. Keep slot validation strict — return success=False with a clear error
       rather than guessing. The orchestrator can then ask the user to clarify.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from core.dataset_context import match_branch
from core.sql_pipeline import SQLResult
from db.mysql_client import execute_query

logger = logging.getLogger(__name__)


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class ExecutorResult:
    """Result from a deterministic operation executor."""

    operation: str
    success: bool
    rows: list[dict] = field(default_factory=list)
    sql: str = ""
    params: tuple = ()
    error: str = ""
    execution_time_ms: float = 0.0
    slots: dict = field(default_factory=dict)

    def to_sql_result(self) -> SQLResult:
        """Adapter to the orchestrator's existing SQLResult contract."""
        return SQLResult(
            success=self.success,
            sql=self.sql,
            params=list(self.params),
            rows=self.rows,
            row_count=len(self.rows),
            error=self.error,
            execution_time_ms=self.execution_time_ms,
            explanation=f"(executor: {self.operation})",
        )


# ── Slot helpers ──────────────────────────────────────────────────────────────

# Backstop slot extraction for batch.
#
# This is NOT a routing gate — the planner has already classified the
# operation by the time dispatch() runs. This regex only fills in a slot
# the planner *dropped* (observed: planner returns op=aggregate but
# filters=[] for "how many students are in batch 2023"). When the planner
# correctly extracts batch into filters, we never touch that value.
#
# Match shapes observed in real queries:
#   "batch 2023" / "batch of 2023" / "in 2023 batch" / "year 2023" /
#   "batch 23" / "batch 2023-2024"
# Restrict to a plausible roll_no prefix window (21–25 today, but allow
# 20–29 to age forward) to avoid matching random 4-digit numbers.
_BATCH_PATTERNS = (
    re.compile(r"\bbatch\s+(?:of\s+)?(20\d{2})\b", re.IGNORECASE),
    re.compile(r"\bbatch\s+(2[0-9])\b",            re.IGNORECASE),
    re.compile(r"\b(20\d{2})\s+batch\b",           re.IGNORECASE),
    re.compile(r"\byear\s+(20\d{2})\b",            re.IGNORECASE),
)


def _extract_batch_from_text(query: str) -> str | None:
    """Backstop: extract a batch year from the query text. Returns "YY" prefix or None."""
    if not query:
        return None
    for pat in _BATCH_PATTERNS:
        m = pat.search(query)
        if m:
            return _batch_prefix(m.group(1))
    return None


def _batch_prefix(batch: Any) -> str | None:
    """
    Map a batch value to the 2-digit roll_no prefix used in WHERE clauses.
    The schema has no batch_year column — batch is derived from roll_no[:2].

    "2024"      → "24"
    "24"        → "24"
    "2024-2025" → "24"   (start year wins)
    """
    if batch is None:
        return None
    s = str(batch).strip()
    if not s:
        return None
    if "-" in s:
        s = s.split("-", 1)[0].strip()
    if len(s) == 4 and s.isdigit():
        return s[-2:]
    if len(s) == 2 and s.isdigit():
        return s
    return None


def _normalize_branch(branch: str | None, query_hint: str = "") -> str | None:
    """Resolve a branch token via the cached canonical branch list."""
    if not branch:
        return None
    try:
        resolved = match_branch(branch, query_hint)
        return resolved or branch
    except Exception:
        return branch


# ── Subject normalization ─────────────────────────────────────────────────────
#
# The planner extracts subjects verbatim from user text ("Math 1", "DBMS",
# "ds (data structrure)"). DB column subject_name uses canonical names
# ("Mathematics-I", "Engineering Mathematics-I", "Database Management System",
# "Data Structure"). Plain LIKE '%subject%' produces 0 matches for almost
# every abbreviation.
#
# This map converts known abbreviations/aliases to a LIST of canonical name
# fragments. _subject_where() then builds an OR'd LIKE clause covering all
# variants present in the DB (the same subject appears with multiple
# subject_codes — BAS103/BAS103H/KAS103T for Math-I, etc., so we match by
# subject_name fragments not subject_code).
#
# Maintenance: when a new abbreviation surfaces in eval logs, add it here.
# The fallback is always a single LIKE '%input%' so an unmapped subject still
# works when the user spells it out.
_SUBJECT_ALIASES: dict[str, list[str]] = {
    # Mathematics (sem-numbered)
    "math 1": ["Mathematics-I", "Mathematics I"],
    "math i": ["Mathematics-I", "Mathematics I"],
    "math1": ["Mathematics-I", "Mathematics I"],
    "maths 1": ["Mathematics-I", "Mathematics I"],
    "maths i": ["Mathematics-I", "Mathematics I"],
    "mathematics 1": ["Mathematics-I", "Mathematics I"],
    "mathematics i": ["Mathematics-I", "Mathematics I"],
    "math 2": ["Mathematics-II", "Mathematics II"],
    "math ii": ["Mathematics-II", "Mathematics II"],
    "math2": ["Mathematics-II", "Mathematics II"],
    "maths 2": ["Mathematics-II", "Mathematics II"],
    "maths ii": ["Mathematics-II", "Mathematics II"],
    "mathematics 2": ["Mathematics-II", "Mathematics II"],
    "mathematics ii": ["Mathematics-II", "Mathematics II"],
    "math 3": ["Mathematics-III", "Mathematics III"],
    "math iii": ["Mathematics-III", "Mathematics III"],
    "math 4": ["Mathematics-IV", "Maths IV"],
    "math iv": ["Mathematics-IV", "Maths IV"],
    "math4": ["Mathematics-IV", "Maths IV"],
    "maths 4": ["Mathematics-IV", "Maths IV"],
    "maths iv": ["Mathematics-IV", "Maths IV"],
    "mathematics 4": ["Mathematics-IV", "Maths IV"],
    "mathematics iv": ["Mathematics-IV", "Maths IV"],
    # Core CS abbreviations
    "dbms": ["Database Management System"],
    "ds": ["Data Structure"],
    "data structure": ["Data Structure"],
    "data structures": ["Data Structure"],
    "daa": ["Design and Analysis of Algorithm"],
    "design and analysis": ["Design and Analysis of Algorithm"],
    "os": ["Operating System"],
    "operating system": ["Operating System"],
    "operating systems": ["Operating System"],
    "cn": ["Computer Networks", "Computer Network"],
    "computer network": ["Computer Networks", "Computer Network"],
    "computer networks": ["Computer Networks", "Computer Network"],
    "se": ["Software Engineering"],
    "software engineering": ["Software Engineering"],
    "wt": ["Web Technology"],
    "web tech": ["Web Technology"],
    "web technology": ["Web Technology"],
    "coa": ["Computer Organization", "Computer Organisation"],
    "computer organization": ["Computer Organization", "Computer Organisation"],
    "computer organisation": ["Computer Organization", "Computer Organisation"],
    "toc": ["Theory of Automata", "Theory of Computation"],
    "tafl": ["Theory of Automata and Formal Languages"],
    "cd": ["Compiler Design"],
    "compiler": ["Compiler Design"],
    "compiler design": ["Compiler Design"],
    "mp": ["Microprocessor"],
    "microprocessor": ["Microprocessor"],
    "pps": ["Programming for Problem Solving"],
    "oop": ["Object Oriented Programming", "Object Oriented System Design"],
    "oops": ["Object Oriented Programming", "Object Oriented System Design"],
    "java": ["Object Oriented Programming with Java"],
    "python": ["Python programming", "Python Programming",
               "Python Language Programming"],
    "ai": ["Artificial Intelligence"],
    "artificial intelligence": ["Artificial Intelligence"],
    "ml": ["Machine Learning"],
    "machine learning": ["Machine Learning"],
    "dl": ["Deep Learning"],
    "deep learning": ["Deep Learning"],
    "da": ["Data Analytics"],
    "data analytics": ["Data Analytics", "Data Analytics and Visualization"],
    "big data": ["Big Data"],
    "iot": ["Internet of Things"],
    "cloud": ["Cloud Computing"],
    "cloud computing": ["Cloud Computing"],
    "nlp": ["Natural language processing", "Natural Language Processing"],
    "cyber security": ["Cyber Security"],
    "cyber": ["Cyber Security"],
    # Sciences
    "chemistry": ["Engineering Chemistry", "Chemistry"],
    "physics": ["Engineering Physics", "Physics"],
    # Electrical / Electronics / Mechanical
    "bee": ["Basic Electrical Engineering",
            "Fundamentals of Electrical Engineering"],
    "basic electrical": ["Basic Electrical Engineering",
                         "Fundamentals of Electrical Engineering"],
    "electrical": ["Basic Electrical Engineering",
                   "Fundamentals of Electrical Engineering"],
    "electronics": ["Electronics Engineering",
                    "Fundamentals of Electronics Engineering",
                    "Basic Electronics Engineering",
                    "Emerging Domain in Electronics Engineering"],
    "mechanical": ["Mechanical Engineering",
                   "Fundamentals of Mechanical Engineering"],
    "fme": ["Fundamentals of Mechanical Engineering"],
    # Humanities / others
    "english": ["English Language", "Technical Communication"],
    "tc": ["Technical Communication"],
    "soft skills": ["Soft Skills", "Soft Skill"],
    "environment": ["Environment and Ecology"],
}


def _normalize_subject_key(s: str) -> str:
    """Lowercase, strip punctuation/whitespace runs, for alias lookup."""
    s = s.lower().strip()
    # Strip parenthesized aliases: "ds (data structrure)" → "ds"
    paren = s.find("(")
    if paren > 0:
        s = s[:paren].strip()
    # Collapse multiple spaces / strip trailing punctuation
    s = re.sub(r"[^\w\s-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _subject_likes(subject: str) -> list[str]:
    """
    Return a list of LIKE patterns (already wrapped in %) that should match
    this subject in subject_name. Falls back to a single substring match
    when no alias is known.
    """
    if not subject:
        return []
    key = _normalize_subject_key(subject)
    fragments = _SUBJECT_ALIASES.get(key)
    if fragments:
        return [f"%{f}%" for f in fragments]
    # Unknown subject → trust the planner's text (user may have typed the
    # canonical name verbatim).
    return [f"%{subject}%"]


def _subject_where(subject: str, table_alias: str = "sm") -> tuple[str, list[str]]:
    """
    Build a WHERE-clause fragment for a subject filter, ORing across all
    known canonical names for an alias. Returns (clause, params).

    Example:
        _subject_where("dbms", "sm")
        → ("(sm.subject_name LIKE %s)",
           ["%Database Management System%"])

        _subject_where("math 1", "sm")
        → ("(sm.subject_name LIKE %s OR sm.subject_name LIKE %s)",
           ["%Mathematics-I%", "%Mathematics I%"])
    """
    likes = _subject_likes(subject)
    if not likes:
        return "", []
    col = f"{table_alias}.subject_name"
    clause = "(" + " OR ".join([f"{col} LIKE %s"] * len(likes)) + ")"
    return clause, likes


def _semester_int(semester: Any) -> int | None:
    """Validate semester ∈ [1, 8]."""
    if semester is None:
        return None
    try:
        s = int(semester)
        return s if 1 <= s <= 8 else None
    except (ValueError, TypeError):
        return None


def _int_or(value: Any, default: int) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _build_where(clauses: list[str]) -> str:
    return ("WHERE " + " AND ".join(clauses)) if clauses else ""


async def _run(operation: str, sql: str, params: tuple, slots: dict) -> ExecutorResult:
    """Execute a parameterized query and wrap it as an ExecutorResult."""
    started = time.perf_counter()
    try:
        rows = await execute_query(sql, params)
        return ExecutorResult(
            operation=operation,
            success=True,
            rows=rows,
            sql=sql.strip(),
            params=params,
            execution_time_ms=(time.perf_counter() - started) * 1000,
            slots=slots,
        )
    except Exception as exc:
        logger.warning("Executor %s failed: %s", operation, exc)
        return ExecutorResult(
            operation=operation,
            success=False,
            sql=sql.strip(),
            params=params,
            error=str(exc),
            execution_time_ms=(time.perf_counter() - started) * 1000,
            slots=slots,
        )


# ── Operations ────────────────────────────────────────────────────────────────

async def top_students(
    branch: str | None = None,
    semester: int | None = None,
    batch: str | None = None,
    n: int = 10,
    query_hint: str = "",
) -> ExecutorResult:
    """
    Top N students ranked by SGPA (when semester is given) or CGPA (across all
    semesters otherwise). Filters: optional branch, optional batch.
    """
    n = max(1, min(_int_or(n, 10), 200))
    sem = _semester_int(semester)
    branch_norm = _normalize_branch(branch, query_hint)
    prefix = _batch_prefix(batch)
    slots = {"branch": branch_norm, "semester": sem, "batch": prefix, "n": n}

    where, params = [], []
    if branch_norm:
        where.append("s.branch = %s")
        params.append(branch_norm)
    if prefix:
        where.append("s.roll_no LIKE %s")
        params.append(f"{prefix}%")

    if sem is not None:
        where.append("sr.semester = %s")
        params.append(sem)
        sql = f"""
            SELECT s.name, s.roll_no, s.branch, sr.semester, sr.sgpa, sr.session
            FROM students s
            JOIN semester_results sr ON s.roll_no = sr.roll_no
            {_build_where(where)}
            ORDER BY sr.sgpa DESC
            LIMIT %s
        """
    else:
        sql = f"""
            SELECT s.name, s.roll_no, s.branch,
                   ROUND(AVG(sr.sgpa), 2) AS cgpa,
                   COUNT(sr.semester)     AS semesters_completed
            FROM students s
            JOIN semester_results sr ON s.roll_no = sr.roll_no
            {_build_where(where)}
            GROUP BY s.roll_no, s.name, s.branch
            ORDER BY cgpa DESC
            LIMIT %s
        """
    params.append(n)
    return await _run("top_students", sql, tuple(params), slots)


async def top_students_in_subject(
    subject: str,
    branch: str | None = None,
    semester: int | None = None,
    batch: str | None = None,
    n: int = 10,
    query_hint: str = "",
) -> ExecutorResult:
    """
    Top N students in a specific subject ranked by total_marks
    (internal + external). Uses subject_marks, not semester_results.
    """
    if not subject:
        return ExecutorResult(
            operation="top_students_in_subject",
            success=False,
            error="subject is required",
        )
    n = max(1, min(_int_or(n, 10), 200))
    sem = _semester_int(semester)
    branch_norm = _normalize_branch(branch, query_hint)
    prefix = _batch_prefix(batch)
    slots = {
        "subject": subject, "branch": branch_norm,
        "semester": sem, "batch": prefix, "n": n,
    }

    subj_clause, subj_params = _subject_where(subject, "sm")
    where, params = [subj_clause], list(subj_params)
    if branch_norm:
        where.append("s.branch = %s")
        params.append(branch_norm)
    if sem is not None:
        where.append("sm.semester = %s")
        params.append(sem)
    if prefix:
        where.append("sm.roll_no LIKE %s")
        params.append(f"{prefix}%")

    sql = f"""
        SELECT s.name, sm.roll_no, s.branch, sm.subject_code, sm.subject_name,
               sm.semester, sm.internal_marks, sm.external_marks, sm.grade,
               (COALESCE(sm.internal_marks, 0) + COALESCE(sm.external_marks, 0)) AS total_marks
        FROM subject_marks sm
        JOIN students s ON sm.roll_no = s.roll_no
        {_build_where(where)}
        ORDER BY total_marks DESC
        LIMIT %s
    """
    params.append(n)
    return await _run("top_students_in_subject", sql, tuple(params), slots)


async def pass_rate(
    semester: int | None = None,
    branch: str | None = None,
    subject: str | None = None,
    batch: str | None = None,
    query_hint: str = "",
) -> ExecutorResult:
    """
    Pass rate (% students whose result is PASS) for a cohort.

    - Without subject: uses semester_results.result_status (semester-level).
    - With subject:    uses subject_marks.grade != 'F' (subject-level).
    """
    sem = _semester_int(semester)
    branch_norm = _normalize_branch(branch, query_hint)
    prefix = _batch_prefix(batch)
    slots = {
        "semester": sem, "branch": branch_norm,
        "subject": subject, "batch": prefix,
    }

    if subject:
        subj_clause, subj_params = _subject_where(subject, "sm")
        where = [subj_clause]
        params: list = list(subj_params)
        if sem is not None:
            where.append("sm.semester = %s"); params.append(sem)
        if branch_norm:
            where.append("s.branch = %s"); params.append(branch_norm)
        if prefix:
            where.append("sm.roll_no LIKE %s"); params.append(f"{prefix}%")
        # Column semantics — kept coherent to avoid the formatter LLM
        # bailing on apparent contradictions (previously had passed>total
        # because passed counted rows but total counted distinct students,
        # so the 7B model said "no data" because the numbers didn't reconcile).
        #
        # PRIMARY answer columns are at the STUDENT level (DISTINCT roll_no):
        #   students_failed = distinct students with at least one F in matching rows
        #   students_passed = distinct students with no F at all
        #   total_students  = distinct students matched
        #
        # Supporting columns are at the ATTEMPT level (rows = student×subject_code):
        #   pass_rate_pct, attempts_passed, attempts_failed, attempts_total
        sql = f"""
            SELECT
                COUNT(DISTINCT CASE WHEN sm.grade = 'F' THEN sm.roll_no END)
                    AS students_failed,
                COUNT(DISTINCT sm.roll_no)
                    - COUNT(DISTINCT CASE WHEN sm.grade = 'F' THEN sm.roll_no END)
                    AS students_passed,
                COUNT(DISTINCT sm.roll_no) AS total_students,
                ROUND(
                    100.0 * SUM(CASE WHEN sm.grade <> 'F' AND sm.grade IS NOT NULL THEN 1 ELSE 0 END)
                          / NULLIF(COUNT(*), 0),
                    2
                ) AS pass_rate_pct,
                SUM(CASE WHEN sm.grade <> 'F' AND sm.grade IS NOT NULL THEN 1 ELSE 0 END)
                    AS attempts_passed,
                SUM(CASE WHEN sm.grade = 'F' THEN 1 ELSE 0 END) AS attempts_failed,
                COUNT(*) AS attempts_total
            FROM subject_marks sm
            JOIN students s ON sm.roll_no = s.roll_no
            {_build_where(where)}
        """
    else:
        # AKTU result_status taxonomy (confirmed from production data):
        #   PASS, CP( 0), CP(0)     = clean pass — zero backlogs
        #   PCP, PWG                = pass with grace / carry paper meta-states
        #   CP( N), CP(N) where N>0 = promoted with N backlogs (subjects to clear)
        #   FAIL                    = outright fail
        #   INCOMPLETE              = absent / incomplete result
        #
        # ONE primary `pass_rate_pct` column — the conservative reading
        # (cleared all subjects, no backlogs). When we returned both
        # pass_rate_clean_pct AND pass_rate_promoted_pct, the formatter
        # LLM picked the higher-looking number AND hallucinated supporting
        # counts. Single canonical rate eliminates that failure mode.
        # The breakdown (cleared / promoted_with_backlogs / failed) is
        # still in supporting columns so the formatter can add context.
        where = []
        params = []
        if sem is not None:
            where.append("sr.semester = %s"); params.append(sem)
        if branch_norm:
            where.append("s.branch = %s"); params.append(branch_norm)
        if prefix:
            where.append("s.roll_no LIKE %s"); params.append(f"{prefix}%")
        # Exclude INCOMPLETE results from the denominator — they're missing data,
        # not academic outcomes.
        where.append("(UPPER(TRIM(sr.result_status)) <> 'INCOMPLETE' "
                     "AND sr.result_status IS NOT NULL "
                     "AND sr.result_status <> '')")
        sql = f"""
            SELECT
                ROUND(
                    100.0 * SUM(CASE WHEN UPPER(TRIM(sr.result_status)) = 'PASS'
                                      OR REPLACE(sr.result_status, ' ', '') = 'CP(0)'
                                     THEN 1 ELSE 0 END)
                          / NULLIF(COUNT(*), 0),
                    2
                ) AS pass_rate_pct,
                SUM(CASE WHEN UPPER(TRIM(sr.result_status)) = 'PASS'
                          OR REPLACE(sr.result_status, ' ', '') = 'CP(0)'
                         THEN 1 ELSE 0 END) AS students_cleared_all_subjects,
                SUM(CASE WHEN UPPER(TRIM(sr.result_status)) IN ('PCP','PWG')
                          OR (sr.result_status LIKE 'CP%%'
                              AND REPLACE(sr.result_status, ' ', '') <> 'CP(0)')
                         THEN 1 ELSE 0 END) AS students_promoted_with_backlogs,
                SUM(CASE WHEN UPPER(TRIM(sr.result_status)) = 'FAIL'
                         THEN 1 ELSE 0 END) AS students_failed,
                COUNT(*) AS total_students
            FROM semester_results sr
            JOIN students s ON sr.roll_no = s.roll_no
            {_build_where(where)}
        """

    return await _run("pass_rate", sql, tuple(params), slots)


async def student_lookup(
    name: str | None = None,
    roll_no: str | None = None,
) -> ExecutorResult:
    """
    Look up a student by exact roll_no or fuzzy name. Returns profile + all
    semester results joined.
    """
    if not name and not roll_no:
        return ExecutorResult(
            operation="student_lookup",
            success=False,
            error="name or roll_no required",
        )
    slots = {"name": name, "roll_no": roll_no}

    if roll_no:
        where, params = ["s.roll_no = %s"], [roll_no]
    else:
        where, params = ["s.name LIKE %s"], [f"%{name}%"]

    sql = f"""
        SELECT s.roll_no, s.name, s.branch, s.course, s.enrollment,
               s.father_name, s.gender,
               sr.semester, sr.session, sr.sgpa, sr.total_marks,
               sr.result_status, sr.total_subjects
        FROM students s
        LEFT JOIN semester_results sr ON s.roll_no = sr.roll_no
        {_build_where(where)}
        ORDER BY s.roll_no, sr.semester
    """
    return await _run("student_lookup", sql, tuple(params), slots)


async def semester_result(
    semester: int,
    name: str | None = None,
    roll_no: str | None = None,
) -> ExecutorResult:
    """
    Per-subject marks for one student in a given semester.
    """
    sem = _semester_int(semester)
    if sem is None:
        return ExecutorResult(
            operation="semester_result",
            success=False,
            error="semester must be 1-8",
        )
    if not name and not roll_no:
        return ExecutorResult(
            operation="semester_result",
            success=False,
            error="name or roll_no required",
        )
    slots = {"semester": sem, "name": name, "roll_no": roll_no}

    if roll_no:
        where, params = ["s.roll_no = %s", "sm.semester = %s"], [roll_no, sem]
    else:
        where, params = ["s.name LIKE %s", "sm.semester = %s"], [f"%{name}%", sem]

    sql = f"""
        SELECT s.name, s.roll_no, sm.semester, sm.subject_code, sm.subject_name,
               sm.type, sm.internal_marks, sm.external_marks, sm.grade,
               (COALESCE(sm.internal_marks, 0) + COALESCE(sm.external_marks, 0)) AS total_marks,
               sm.back_paper
        FROM subject_marks sm
        JOIN students s ON sm.roll_no = s.roll_no
        {_build_where(where)}
        ORDER BY sm.subject_code
    """
    return await _run("semester_result", sql, tuple(params), slots)


async def average_marks(
    subject: str,
    semester: int | None = None,
    branch: str | None = None,
    batch: str | None = None,
    query_hint: str = "",
) -> ExecutorResult:
    """
    Average total marks (internal + external) for a subject across a cohort.
    """
    if not subject:
        return ExecutorResult(
            operation="average_marks",
            success=False,
            error="subject is required",
        )
    sem = _semester_int(semester)
    branch_norm = _normalize_branch(branch, query_hint)
    prefix = _batch_prefix(batch)
    slots = {"subject": subject, "semester": sem, "branch": branch_norm, "batch": prefix}

    subj_clause, subj_params = _subject_where(subject, "sm")
    where, params = [subj_clause], list(subj_params)
    if sem is not None:
        where.append("sm.semester = %s"); params.append(sem)
    if branch_norm:
        where.append("s.branch = %s"); params.append(branch_norm)
    if prefix:
        where.append("sm.roll_no LIKE %s"); params.append(f"{prefix}%")

    # UNION'd query: row 1 is the weighted overall (across all subject_code
    # variants matching the LIKE pattern); rows 2..N are the per-variant
    # breakdown. The user's most likely intent for "average marks in DBMS"
    # is the overall — but the per-variant detail is still useful, so we
    # surface both and let the formatter lead with row 1 (subject_code=NULL,
    # subject_name="(overall)").
    where_sql = _build_where(where)
    sql = f"""
        SELECT * FROM (
            SELECT '(overall)' AS subject_name,
                   NULL         AS subject_code,
                   ROUND(AVG(COALESCE(sm.internal_marks, 0) + COALESCE(sm.external_marks, 0)), 2) AS avg_total_marks,
                   ROUND(AVG(sm.internal_marks), 2) AS avg_internal,
                   ROUND(AVG(sm.external_marks), 2) AS avg_external,
                   COUNT(*) AS student_count,
                   0 AS sort_key
            FROM subject_marks sm
            JOIN students s ON sm.roll_no = s.roll_no
            {where_sql}

            UNION ALL

            SELECT sm.subject_name, sm.subject_code,
                   ROUND(AVG(COALESCE(sm.internal_marks, 0) + COALESCE(sm.external_marks, 0)), 2) AS avg_total_marks,
                   ROUND(AVG(sm.internal_marks), 2) AS avg_internal,
                   ROUND(AVG(sm.external_marks), 2) AS avg_external,
                   COUNT(*) AS student_count,
                   1 AS sort_key
            FROM subject_marks sm
            JOIN students s ON sm.roll_no = s.roll_no
            {where_sql}
            GROUP BY sm.subject_name, sm.subject_code
        ) AS combined
        ORDER BY sort_key, avg_total_marks DESC
    """
    # The WHERE clause is reused for both halves of the UNION.
    return await _run("average_marks", sql, tuple(params + params), slots)


async def count_query(
    branch: str | None = None,
    semester: int | None = None,
    batch: str | None = None,
    gender: str | None = None,
    course: str | None = None,
    query_hint: str = "",
) -> ExecutorResult:
    """
    Count students matching a filter set. No subject/marks filter — that's a
    different shape that goes through threshold_query or the LLM-SQL path.
    """
    sem = _semester_int(semester)
    branch_norm = _normalize_branch(branch, query_hint)
    prefix = _batch_prefix(batch)
    slots = {
        "branch": branch_norm, "semester": sem, "batch": prefix,
        "gender": gender, "course": course,
    }

    where, params = [], []
    if branch_norm:
        where.append("s.branch = %s"); params.append(branch_norm)
    if prefix:
        where.append("s.roll_no LIKE %s"); params.append(f"{prefix}%")
    if gender:
        where.append("s.gender = %s"); params.append(gender)
    if course:
        where.append("s.course = %s"); params.append(course)

    # If semester is specified, we count students with a semester_results row
    # for that semester. Otherwise we count distinct students.
    if sem is not None:
        where.append("sr.semester = %s"); params.append(sem)
        sql = f"""
            SELECT COUNT(DISTINCT s.roll_no) AS student_count
            FROM students s
            JOIN semester_results sr ON s.roll_no = sr.roll_no
            {_build_where(where)}
        """
    else:
        sql = f"""
            SELECT COUNT(*) AS student_count
            FROM students s
            {_build_where(where)}
        """
    return await _run("count_query", sql, tuple(params), slots)


async def backlog_query(
    name: str | None = None,
    roll_no: str | None = None,
    branch: str | None = None,
    semester: int | None = None,
    batch: str | None = None,
    query_hint: str = "",
) -> ExecutorResult:
    """
    Subjects with grade='F' (backlogs) for a student, or backlog counts by
    cohort if name/roll_no is absent.
    """
    sem = _semester_int(semester)
    branch_norm = _normalize_branch(branch, query_hint)
    prefix = _batch_prefix(batch)
    slots = {
        "name": name, "roll_no": roll_no, "semester": sem,
        "branch": branch_norm, "batch": prefix,
    }

    where = ["sm.grade = 'F'"]
    params: list = []

    if roll_no:
        where.append("sm.roll_no = %s"); params.append(roll_no)
    elif name:
        where.append("s.name LIKE %s"); params.append(f"%{name}%")
    if branch_norm:
        where.append("s.branch = %s"); params.append(branch_norm)
    if sem is not None:
        where.append("sm.semester = %s"); params.append(sem)
    if prefix:
        where.append("sm.roll_no LIKE %s"); params.append(f"{prefix}%")

    if name or roll_no:
        sql = f"""
            SELECT s.name, sm.roll_no, sm.semester, sm.subject_code,
                   sm.subject_name, sm.grade, sm.back_paper,
                   sm.internal_marks, sm.external_marks
            FROM subject_marks sm
            JOIN students s ON sm.roll_no = s.roll_no
            {_build_where(where)}
            ORDER BY sm.semester, sm.subject_code
        """
    else:
        # Aggregate: backlog counts per student
        sql = f"""
            SELECT s.name, sm.roll_no, s.branch,
                   COUNT(*) AS backlog_count,
                   GROUP_CONCAT(DISTINCT sm.subject_name ORDER BY sm.subject_name) AS subjects
            FROM subject_marks sm
            JOIN students s ON sm.roll_no = s.roll_no
            {_build_where(where)}
            GROUP BY sm.roll_no, s.name, s.branch
            ORDER BY backlog_count DESC
            LIMIT 200
        """
    return await _run("backlog_query", sql, tuple(params), slots)


# ── Dispatch ──────────────────────────────────────────────────────────────────

async def dispatch(query: str, route_result: Any) -> ExecutorResult | None:
    """
    Decide whether the planner's output maps to a registered executor.

    Returns an ExecutorResult on dispatch, or None to signal "no match — fall
    through to the LLM-SQL path." Conservative by design: any ambiguity in the
    slots means we decline and let the existing pipeline try.

    Inputs read from route_result:
        - .operation     ("list" | "aggregate" | "lookup" | "comparison" | ...)
        - .aggregation   ("avg" | "count" | "pass_rate" | ...)
        - .filters       {"branch", "semester", "subject_name", "batch", "name", "roll_no", ...}
        - .entities      raw planner extraction (for top_n etc.)
    """
    operation = (getattr(route_result, "operation", "") or "").lower()
    aggregation = (getattr(route_result, "aggregation", "") or "").lower()
    raw_filters = getattr(route_result, "filters", None) or {}
    filters: dict = dict(raw_filters) if isinstance(raw_filters, dict) else {}

    # RouteResult.entities is `list[str]` (human-readable mentions like
    # "semester 4"), not a structured dict. Structured planner output lives
    # in `filters` after _plan_entities_to_filters. Only treat entities as a
    # dict in the (uncommon) case where a caller built it that way.
    raw_entities = getattr(route_result, "entities", None) or {}
    entities: dict = raw_entities if isinstance(raw_entities, dict) else {}

    if not operation:
        return None

    branch = filters.get("branch")
    semester = filters.get("semester")
    subject = filters.get("subject_name") or entities.get("subject")
    batch = filters.get("batch") or entities.get("batch")
    # Backstop: planner sometimes classifies the operation but drops the
    # batch slot ("how many students are in batch 2023" → op=aggregate but
    # filters=[]). Pick up the missing slot from raw query text. We NEVER
    # override a batch the planner did set.
    if not batch:
        batch_from_text = _extract_batch_from_text(query)
        if batch_from_text:
            batch = batch_from_text
            logger.info("dispatch: batch slot filled from text backstop -> %s", batch)
    name = filters.get("name") or entities.get("student_name")
    roll_no = filters.get("roll_no") or entities.get("roll_no")
    gender = filters.get("gender") or entities.get("gender")
    course = filters.get("course") or entities.get("course")
    top_n = filters.get("top_n") or entities.get("top_n")

    # ── list: top students ──
    if operation == "list":
        if not top_n:
            return None  # ambiguous — let LLM-SQL try
        if subject:
            return await top_students_in_subject(
                subject=subject, branch=branch, semester=semester,
                batch=batch, n=top_n, query_hint=query,
            )
        return await top_students(
            branch=branch, semester=semester, batch=batch,
            n=top_n, query_hint=query,
        )

    # ── aggregate ──
    if operation == "aggregate":
        if aggregation == "pass_rate" or aggregation == "fail_rate":
            return await pass_rate(
                semester=semester, branch=branch, subject=subject,
                batch=batch, query_hint=query,
            )
        if aggregation == "avg" and subject:
            return await average_marks(
                subject=subject, semester=semester, branch=branch,
                batch=batch, query_hint=query,
            )
        if aggregation == "count":
            return await count_query(
                branch=branch, semester=semester, batch=batch,
                gender=gender, course=course, query_hint=query,
            )
        return None  # unhandled aggregate — fall through

    # ── lookup ──
    if operation == "lookup":
        if not (name or roll_no):
            return None
        if semester is not None:
            return await semester_result(
                semester=semester, name=name, roll_no=roll_no,
            )
        # Generic "show me student X" — backlog query if user mentioned backlog
        q_low = query.lower()
        if "backlog" in q_low or "back paper" in q_low or "failed" in q_low:
            return await backlog_query(
                name=name, roll_no=roll_no, branch=branch,
                semester=semester, batch=batch, query_hint=query,
            )
        return await student_lookup(name=name, roll_no=roll_no)

    # ── comparison: handled by LLM-SQL for now ──
    # Branch-vs-branch and batch-vs-batch comparisons need multi-result
    # synthesis. Defer to a follow-up iteration so we don't ship a half-baked
    # comparison executor that hides accuracy issues behind dispatch.
    return None

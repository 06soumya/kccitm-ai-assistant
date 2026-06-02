"""
Dataset context loaded once at startup, cached, and injected into every LLM
prompt that needs to know what the dataset contains (router, RAG answer
generator, query understander).

The small local LLM cannot route or summarise correctly when it doesn't know
what the full dataset looks like — it only ever sees the query and (in RAG)
the retrieved chunks. This module gives every LLM call a stable, accurate
view of the data it's reasoning about.
"""
from __future__ import annotations

import logging
import re

from db.mysql_client import execute_query

logger = logging.getLogger(__name__)

_FALLBACK = (
    "KCCITM student academic database. Per-student records include name, "
    "roll_no, branch, course, semester, session, sgpa, total_marks, "
    "result_status, gender, and subject_marks. Aggregate counts unavailable."
)

_dataset_context: str = _FALLBACK
_user_summary: str = ""
_loaded: bool = False
_branches: list[str] = []
_courses: list[str] = []
_sessions: list[str] = []
_subject_codes: set[str] = set()


async def load_dataset_context(milvus_chunk_count: int | None = None) -> str:
    """
    Build the dataset summary from MySQL (and optionally Milvus stats) and
    cache it. Idempotent — safe to call multiple times. Returns the cached
    string. Never raises; on failure the fallback stays in place.
    """
    global _dataset_context, _loaded, _branches, _courses, _sessions, _subject_codes
    try:
        s_count = (await execute_query("SELECT COUNT(*) AS n FROM students"))[0]["n"]

        branches = [
            r["branch"] for r in await execute_query(
                "SELECT DISTINCT branch FROM students "
                "WHERE branch IS NOT NULL AND branch <> '' ORDER BY branch"
            )
        ]
        courses = [
            r["course"] for r in await execute_query(
                "SELECT DISTINCT course FROM students "
                "WHERE course IS NOT NULL AND course <> '' ORDER BY course"
            )
        ]
        sessions = [
            r["session"] for r in await execute_query(
                "SELECT DISTINCT session FROM semester_results "
                "WHERE session IS NOT NULL AND session <> '' ORDER BY session"
            )
        ]
        _branches = branches
        _courses = courses
        _sessions = sessions
        try:
            code_rows = await execute_query(
                "SELECT DISTINCT subject_code FROM subject_marks "
                "WHERE subject_code IS NOT NULL AND subject_code <> ''"
            )
            _subject_codes = {r["subject_code"].upper() for r in code_rows if r.get("subject_code")}
        except Exception as exc:
            logger.debug("Could not load subject codes: %s", exc)
            _subject_codes = set()
        sem_row = (await execute_query(
            "SELECT MIN(semester) AS sem_min, MAX(semester) AS sem_max "
            "FROM semester_results"
        ))[0]
        sr_count = (await execute_query(
            "SELECT COUNT(*) AS n FROM semester_results"
        ))[0]["n"]

        lines = [
            "KCCITM ACADEMIC DATASET (use to ground every answer):",
            f"- Total students: {s_count}",
            f"- Total semester result rows: {sr_count}",
            f"- Branches ({len(branches)}): {', '.join(branches)}",
            f"- Courses ({len(courses)}): {', '.join(courses)}",
            f"- Semesters: {sem_row['sem_min']} to {sem_row['sem_max']}",
            f"- Sessions: {', '.join(sessions)}",
        ]
        if milvus_chunk_count is not None:
            lines.append(f"- Indexed vector chunks: {milvus_chunk_count}")
        lines.append(
            "Per-student fields: name, roll_no, branch, course, semester, "
            "session, sgpa, total_marks, result_status, gender, subject_marks."
        )

        _dataset_context = "\n".join(lines)

        # Build the user-facing markdown summary (different audience than the
        # LLM-facing context — terser, friendlier formatting, no "ground your
        # answers" framing).
        global _user_summary
        branch_list = "\n".join(f"- {b}" for b in branches)
        chunk_line = (
            f"\n- **Indexed vector chunks:** {milvus_chunk_count:,}"
            if milvus_chunk_count is not None else ""
        )
        _user_summary = (
            f"This database contains academic records for **{s_count:,} students** "
            f"at KCCITM, across **{len(branches)} branches**, "
            f"**{len(courses)} courses**, and **{len(sessions)} sessions**.\n\n"
            f"**Branches ({len(branches)}):**\n{branch_list}\n\n"
            f"**Courses:** {', '.join(courses)}\n\n"
            f"**Semesters:** {sem_row['sem_min']} to {sem_row['sem_max']}\n\n"
            f"**Sessions ({len(sessions)}):** {', '.join(sessions)}\n\n"
            f"**Per-student fields:** name, roll number, branch, course, "
            f"semester, session, SGPA, total marks, result status, gender, "
            f"and subject-wise marks (theory + practical).\n\n"
            f"**Total semester result rows:** {sr_count:,}"
            f"{chunk_line}\n\n"
            f"You can ask questions like:\n"
            f"- *\"How many students are in [branch]?\"*\n"
            f"- *\"Top 10 students in semester 4 by SGPA\"*\n"
            f"- *\"Tell me about [student name]\"*\n"
            f"- *\"Average SGPA in [branch]\"*\n"
            f"- *\"Compare pass rates between branches\"*"
        )

        _loaded = True
        logger.info(
            "Dataset context loaded: %d students, %d branches, %d sessions",
            s_count, len(branches), len(sessions),
        )
        return _dataset_context
    except Exception as exc:
        logger.warning("Failed to load dataset context: %s — using fallback", exc)
        return _dataset_context


def get_dataset_context() -> str:
    """Return the cached dataset context (or fallback if not yet loaded)."""
    return _dataset_context


def get_user_summary() -> str:
    """
    Return the user-facing markdown summary of the dataset.

    Used by the dataset meta-question short-circuit in the orchestrator —
    a templated answer for "what kind of data do you have" class queries.
    Empty string if not loaded yet.
    """
    return _user_summary


def is_loaded() -> bool:
    """True if the real (non-fallback) context has been loaded."""
    return _loaded


def get_branches() -> list[str]:
    """Cached list of real branch names (uppercase as stored in MySQL)."""
    return list(_branches)


def get_courses() -> list[str]:
    """Cached list of real course names."""
    return list(_courses)


def get_sessions() -> list[str]:
    """Cached list of real session strings (e.g. '2021-22(REGULAR)')."""
    return list(_sessions)


def get_subject_codes() -> set[str]:
    """Cached set of all subject codes seen in subject_marks (uppercased)."""
    return set(_subject_codes)


# Stopwords that shouldn't drive branch selection on their own.
_BRANCH_STOPWORDS = {
    "AND", "OR", "THE", "OF", "IN", "FOR", "WITH",
    "BRANCH", "DEPARTMENT", "DEPT", "STREAM",
    "AVERAGE", "AVG", "MEAN", "TOTAL", "COUNT",
    "STUDENTS", "STUDENT", "TOP", "BOTTOM",
}


def _tokens(s: str) -> set[str]:
    """Uppercased alphanumeric word tokens of `s`, stopwords removed."""
    return {
        w for w in re.findall(r"[A-Za-z0-9]+", s.upper())
        if w not in _BRANCH_STOPWORDS
    }


def match_branch(token: str, query: str = "") -> str | None:
    """
    Match a user-supplied branch token (with optional query context) against
    the cached real branch list. Returns the canonical branch name as stored
    in MySQL, or None if no good match.

    Algorithm:
      1. Exact case-insensitive match → return immediately.
      2. Otherwise, score each branch by:
         - specificity = number of branch tokens present in (token + query)
         - coverage    = specificity / len(branch tokens)
         Sort by (specificity desc, coverage desc) and pick the top.
      3. Accept the top match only if coverage >= 0.5 OR specificity >= 2.
         This biases toward more specific branches (e.g. "data science"
         picks the DS sub-branch over the parent CSE branch).

    Stopwords like AND/OF/BRANCH are ignored so they don't tilt the score.
    """
    if not token or not _branches:
        return None

    cleaned = token.strip()
    if not cleaned:
        return None

    # Score every branch by overlap with (token + query). No exact-match
    # short-circuit — a query like "average in CSE Data Science" passes
    # token="COMPUTER SCIENCE AND ENGINEERING" (an exact CSE match) but the
    # query mentions DATA SCIENCE, so the DS sub-branch is actually more
    # specific and the score sort needs a chance to pick it.
    text_tokens = _tokens(f"{cleaned} {query}")
    if not text_tokens:
        return None

    scored: list[tuple[int, float, str]] = []
    for b in _branches:
        b_tokens = _tokens(b)
        if not b_tokens:
            continue
        overlap = b_tokens & text_tokens
        specificity = len(overlap)
        coverage = specificity / len(b_tokens)
        if specificity == 0:
            continue
        scored.append((specificity, coverage, b))

    if not scored:
        return None

    scored.sort(key=lambda x: (-x[0], -x[1]))
    top_spec, top_cov, top_branch = scored[0]

    if top_cov >= 0.5 or top_spec >= 2:
        return top_branch
    return None

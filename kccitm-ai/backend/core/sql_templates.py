"""
SQL template library — consulted by sql_pipeline when the planner signals
`needs_templates=True` (or the SQL validator rejects a first attempt).

Replaces three hardcoded paths from sql_pipeline:
  - MANDATORY subject template (forced shape for subject queries)
  - 7 regex-driven pattern hints (percentage, improved, comparison, etc.)
  - _SUBJECT_DETECT keyword map (DBMS → '%Database Management%')

These templates are SHOWN to the LLM as reference shapes — not enforced.
The LLM is free to write SQL from scratch; the templates are there to
nudge the small local model toward known-good shapes on complex queries.

Template retrieval is EMBEDDING-DRIVEN: each template's description +
when_to_use string is embedded once (lazily, on first call), and at
query time we rank by cosine similarity. No keyword scoring, no regex
"boosts" — the embedder learns which template shape matches which
question. Add a new template (description + SQL) and it joins the
retrieval pool with zero code changes.
"""
from __future__ import annotations

import asyncio
import logging

import numpy as np

logger = logging.getLogger(__name__)

# ── Template registry ────────────────────────────────────────────────────────

_TEMPLATES: dict[str, dict] = {

    # 1. Top-N students for a specific subject
    "subject_topn": {
        "name": "subject_topn",
        "description": "Top N students for a specific subject, ordered by total marks.",
        "when_to_use": "Query mentions a subject (DBMS, OS, math) AND asks for top/best/highest students.",
        "triggers": [
            "top", "best", "highest", "rank", "bottom", "worst", "lowest",
        ],
        "sql": """SELECT s.name, sm.roll_no, s.branch, sm.subject_code, sm.subject_name,
       sm.internal_marks, sm.external_marks,
       (COALESCE(sm.internal_marks,0) + COALESCE(sm.external_marks,0)) AS total_marks,
       sm.grade
FROM subject_marks sm
JOIN students s ON sm.roll_no = s.roll_no
WHERE sm.subject_name LIKE '%<SUBJECT>%'
ORDER BY total_marks DESC
LIMIT <N>""",
        "notes": "Substitute <SUBJECT> with a fragment from subject_name (LIKE matches partial). "
                 "For 'bottom/worst' use ASC. Add `AND s.branch = '<BRANCH>'` or "
                 "`AND sm.semester = <N>` if the user named one.",
    },

    # 2. Aggregate (avg / sum / count) for a subject
    "subject_aggregate": {
        "name": "subject_aggregate",
        "description": "Compute an aggregate (AVG / SUM / COUNT) for a specific subject.",
        "when_to_use": "Query asks for the average / count / sum of marks in a subject.",
        "triggers": [
            "average", "avg", "mean", "count", "how many", "total",
        ],
        "sql": """SELECT ROUND(AVG(COALESCE(sm.internal_marks,0)
                  + COALESCE(sm.external_marks,0)), 2) AS avg_total_marks,
       COUNT(*) AS n_students
FROM subject_marks sm
JOIN students s ON sm.roll_no = s.roll_no
WHERE sm.subject_name LIKE '%<SUBJECT>%'""",
        "notes": "Swap AVG for SUM or COUNT depending on the question. "
                 "Add `AND s.branch = '<BRANCH>'` / `AND sm.semester = <N>` to filter.",
    },

    # 3. Pass / fail rate — percentage of students passed
    "pass_rate": {
        "name": "pass_rate",
        "description": "Pass percentage for a group of students.",
        "when_to_use": "Query asks for pass rate / fail rate / percentage passed.",
        "triggers": [
            "pass rate", "pass percentage", "percent passed", "percentage of",
            "fail rate", "failure rate", "percentage failed",
        ],
        "sql": """SELECT ROUND(
  SUM(CASE WHEN sr.result_status IN ('PASS','PCP','PWG')
              OR sr.result_status LIKE 'CP(% 0)'
           THEN 1 ELSE 0 END) * 100.0
  / COUNT(*), 2) AS pass_rate_pct,
  COUNT(*) AS total_students
FROM semester_results sr
JOIN students s ON s.roll_no = sr.roll_no
WHERE 1=1
  -- AND s.branch = '<BRANCH>' / AND sr.semester = <N> / AND sr.session = '<SESSION>'""",
        "notes": "MUST use 100.0 (not 100) for decimal division. "
                 "PASS-like statuses: 'PASS','PCP','PWG','CP(... 0)'. Anything else is fail. "
                 "Replace `IN (...)` with `NOT IN (...)` for fail rate.",
    },

    # 4. SGPA improved / dropped between two semesters
    "improved_dropped": {
        "name": "improved_dropped",
        "description": "Students whose SGPA improved or dropped between two semesters (self-join).",
        "when_to_use": "Query mentions 'improved', 'dropped', 'increased', 'decreased', 'trend', 'change' between specific semesters.",
        "triggers": [
            "improved", "dropped", "increased", "decreased", "change",
            "trend", "growth", "between semester",
        ],
        "sql": """SELECT s.name, s.roll_no, s.branch,
       sr1.sgpa AS sem<A>_sgpa,
       sr2.sgpa AS sem<B>_sgpa,
       ROUND(sr2.sgpa - sr1.sgpa, 2) AS change
FROM students s
JOIN semester_results sr1 ON s.roll_no = sr1.roll_no AND sr1.semester = <A>
JOIN semester_results sr2 ON s.roll_no = sr2.roll_no AND sr2.semester = <B>
WHERE sr2.sgpa > sr1.sgpa     -- flip to < for 'dropped'
ORDER BY change DESC""",
        "notes": "Self-join semester_results on roll_no. "
                 "For 'improved' use `sr2.sgpa > sr1.sgpa`; for 'dropped' `<`.",
    },

    # 5. Which X has highest / lowest Y
    "which_x_extreme": {
        "name": "which_x_extreme",
        "description": "Find the branch / batch / semester / subject with the highest or lowest value of a metric.",
        "when_to_use": "Query asks 'which branch/batch/semester/subject has the highest/lowest …'",
        "triggers": [
            "which branch", "which batch", "which semester", "which subject",
            "highest", "lowest", "best", "worst", "most", "least",
        ],
        "sql": """SELECT s.branch, ROUND(AVG(sr.sgpa), 2) AS metric
FROM students s
JOIN semester_results sr ON s.roll_no = sr.roll_no
GROUP BY s.branch
ORDER BY metric DESC      -- ASC for lowest/worst
LIMIT 1""",
        "notes": "Replace `s.branch` and `AVG(sr.sgpa)` with the actual group column "
                 "and metric the user asked about. ASC for lowest/worst/least; "
                 "DESC for highest/best/most.",
    },

    # 6. Students with a condition across ALL their semester rows
    "students_condition_across_rows": {
        "name": "students_condition_across_rows",
        "description": "Students who satisfy a condition across every one of their semester rows (e.g. never failed any semester).",
        "when_to_use": "Query says 'students who [never failed / passed all / got A in every / etc.]'",
        "triggers": [
            "never failed", "always passed", "never got", "all semester",
            "every semester", "no backlog", "zero backlog",
        ],
        "sql": """SELECT s.name, s.roll_no, s.branch,
       COUNT(sr.semester) AS sem_count
FROM students s
JOIN semester_results sr ON s.roll_no = sr.roll_no
GROUP BY s.roll_no, s.name, s.branch
HAVING SUM(CASE
            WHEN sr.result_status NOT IN ('PASS','PCP','PWG')
             AND sr.result_status NOT LIKE 'CP(% 0)'
            THEN 1 ELSE 0 END) = 0""",
        "notes": "Use GROUP BY + HAVING. The HAVING clause counts how many rows "
                 "violate the condition — must be 0 for 'never/always/all' queries.",
    },

    # 7. Subjects where X% of students got grade Y
    "subjects_grade_threshold": {
        "name": "subjects_grade_threshold",
        "description": "Subjects where a given percentage of students received a specific grade.",
        "when_to_use": "Query talks about subjects with a grade distribution threshold (e.g. 'subjects where >50% got F').",
        "triggers": [
            "subjects where", "subjects with", "grade distribution",
        ],
        "sql": """SELECT sm.subject_name, sm.subject_code,
       ROUND(SUM(CASE WHEN sm.grade = '<GRADE>' THEN 1 ELSE 0 END) * 100.0
             / COUNT(*), 2) AS pct_with_grade,
       COUNT(*) AS total_students
FROM subject_marks sm
GROUP BY sm.subject_name, sm.subject_code
HAVING pct_with_grade > <THRESHOLD>
ORDER BY pct_with_grade DESC""",
        "notes": "Replace <GRADE> with the grade in question (F, A, A+, etc.). "
                 "<THRESHOLD> is the percentage threshold.",
    },

    # 8. Compare two groups (gender / branch / batch / etc.)
    "compare_groups": {
        "name": "compare_groups",
        "description": "Compare a metric across two or more groups in one query using GROUP BY.",
        "when_to_use": "Query says 'compare X and Y' / 'X vs Y' / 'between A and B' across a dimension.",
        "triggers": [
            "compare", " vs ", "versus", "difference between",
        ],
        "sql": """SELECT s.<GROUP_COL>, ROUND(AVG(sr.sgpa), 2) AS avg_sgpa,
       COUNT(DISTINCT s.roll_no) AS n_students
FROM students s
JOIN semester_results sr ON s.roll_no = sr.roll_no
GROUP BY s.<GROUP_COL>
ORDER BY avg_sgpa DESC""",
        "notes": "<GROUP_COL> is the dimension to compare: branch, gender, course. "
                 "For BATCH comparison use the dedicated `compare_batches` template "
                 "below — batch is not a column, it's derived from roll_no.",
    },

    # 8b. Compare batches — batch is derived from roll_no prefix, NOT a column.
    # This is the shape cmp-03 was failing on: the LLM kept missing the
    # CASE WHEN derivation and the SQL would either error or return one batch.
    "compare_batches": {
        "name": "compare_batches",
        "description": "Compare a metric (avg SGPA, pass rate, count) across two or more batches. Batch is derived from roll_no LIKE 'YY%' — there is no batch column.",
        "when_to_use": "Query mentions two or more batches AND asks to compare/contrast (compare batch 2023 and 2024, batch 21 vs batch 22, etc.).",
        "triggers": [
            "compare batch", "batch comparison", "batches",
            "batch 2021", "batch 2022", "batch 2023", "batch 2024", "batch 2025",
            "across batches", "between batch",
        ],
        "sql": """SELECT
    CASE
        WHEN s.roll_no LIKE '21%' THEN '2021'
        WHEN s.roll_no LIKE '22%' THEN '2022'
        WHEN s.roll_no LIKE '23%' THEN '2023'
        WHEN s.roll_no LIKE '24%' THEN '2024'
        WHEN s.roll_no LIKE '25%' THEN '2025'
    END AS batch,
    ROUND(AVG(sr.sgpa), 2) AS avg_sgpa,
    COUNT(DISTINCT s.roll_no) AS n_students
FROM students s
JOIN semester_results sr ON s.roll_no = sr.roll_no
WHERE sr.sgpa > 0
  AND (s.roll_no LIKE '23%' OR s.roll_no LIKE '24%')   -- restrict to user-named batches
GROUP BY batch
HAVING batch IS NOT NULL
ORDER BY batch""",
        "notes": "Always derive batch from roll_no LIKE 'YY%' where YY = last two "
                 "digits of the year (23 = batch 2023). NEVER reference a "
                 "`batch` or `batch_year` column — they do not exist. The WHERE "
                 "clause must restrict to the user-named batches so unrelated "
                 "batches don't pollute the result. For pass-rate comparison "
                 "swap AVG(sr.sgpa) for `ROUND(SUM(CASE WHEN result_status IN "
                 "('PASS','PCP','PWG') THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2)`.",
    },

    # 9. Per-branch / per-semester topper or aggregate
    "per_group_aggregate": {
        "name": "per_group_aggregate",
        "description": "Aggregate (e.g. topper, average) computed separately for each branch/semester/etc.",
        "when_to_use": "Query says 'per branch', 'each branch', 'by semester', 'every batch'.",
        "triggers": [
            "per branch", "each branch", "per semester", "each semester",
            "by branch", "by semester", "every branch", "branch wise",
            "semester wise",
        ],
        "sql": """SELECT s.branch, s.name, s.roll_no, sr.sgpa
FROM students s
JOIN semester_results sr ON s.roll_no = sr.roll_no
WHERE (s.branch, sr.sgpa) IN (
    SELECT s2.branch, MAX(sr2.sgpa)
    FROM students s2
    JOIN semester_results sr2 ON s2.roll_no = sr2.roll_no
    GROUP BY s2.branch
)
ORDER BY s.branch""",
        "notes": "For 'topper per branch' the subquery picks the max per group. "
                 "For 'average per branch' just `GROUP BY s.branch` with AVG. "
                 "Do NOT use window functions — they don't work reliably in all MySQL versions here.",
    },

    # 10. Trend across semesters
    "trend_across_semesters": {
        "name": "trend_across_semesters",
        "description": "Metric evolution across semesters (line/trend data).",
        "when_to_use": "Query says 'trend', 'across semesters', 'semester wise', 'over time'.",
        "triggers": [
            "trend", "across semester", "across all semester",
            "semesterwise", "over time", "progression",
        ],
        "sql": """SELECT sr.semester,
       ROUND(AVG(sr.sgpa), 2) AS avg_sgpa,
       COUNT(*) AS n_students
FROM semester_results sr
JOIN students s ON sr.roll_no = s.roll_no
WHERE 1=1
  -- AND s.branch = '<BRANCH>' / AND sr.session = '<SESSION>'
GROUP BY sr.semester
ORDER BY sr.semester""",
        "notes": "GROUP BY semester, ORDER BY semester. For batch filter use "
                 "`AND sr.roll_no LIKE '<YY>%'`.",
    },

    # 11. Nested filter — "of those who X, also Y"
    "nested_filter": {
        "name": "nested_filter",
        "description": "Two-condition filter where the second applies only to those who matched the first.",
        "when_to_use": "Query says 'of those who …, also …' / 'among those who … …' / 'who also'.",
        "triggers": [
            "of those who", "among those", "also failed", "that also",
            "who also",
        ],
        "sql": """SELECT COUNT(*) AS matched
FROM students s
WHERE s.roll_no IN (
    SELECT DISTINCT roll_no FROM <FIRST_CONDITION_QUERY>
)
AND s.roll_no IN (
    SELECT DISTINCT roll_no FROM <SECOND_CONDITION_QUERY>
)""",
        "notes": "Use IN-subqueries to intersect. Easier to reason about than "
                 "self-joins. Substitute the inner queries with the actual "
                 "conditions the user asked about.",
    },
}


# ── Public API ──────────────────────────────────────────────────────────────

def get_template(name: str) -> dict | None:
    """Return a single template by name, or None."""
    return _TEMPLATES.get(name)


# ── Embedding-based retrieval ───────────────────────────────────────────────
#
# Each template's description+when_to_use string is embedded once on first
# call. The query is embedded at call time and templates are ranked by
# cosine similarity. No keyword scoring, no per-template boost lists —
# adding a new template is just adding a row.

_TEMPLATE_EMBEDDINGS: dict[str, np.ndarray] = {}
_EMBED_LOCK = asyncio.Lock()


def _template_corpus_text(t: dict) -> str:
    """The text we embed for retrieval — description carries the semantic
    meaning, when_to_use lists the situations, notes adds detail."""
    return " ".join([
        t.get("description", ""),
        t.get("when_to_use", ""),
        t.get("notes", ""),
    ]).strip()


async def _ensure_template_embeddings(llm) -> None:
    """Embed each template once. Idempotent — safe to call on every query."""
    if _TEMPLATE_EMBEDDINGS:
        return
    async with _EMBED_LOCK:
        if _TEMPLATE_EMBEDDINGS:   # double-check after lock
            return
        for name, t in _TEMPLATES.items():
            text = _template_corpus_text(t)
            if not text:
                continue
            try:
                emb = await llm.embed(text)
                _TEMPLATE_EMBEDDINGS[name] = np.array(emb, dtype=np.float32)
            except Exception as exc:
                logger.warning(
                    "Template embed failed for %s: %s — skipping", name, exc,
                )


async def get_relevant_templates(
    llm,
    query: str,
    operation: str = "",
    aggregation: str = "",
    top_k: int = 3,
    min_score: float = 0.45,
) -> list[dict]:
    """
    Rank templates by embedding cosine similarity to the query.

    The planner's `operation` and `aggregation` are appended to the query
    text before embedding — that way the planner's structured understanding
    of the query shapes retrieval WITHOUT us hardcoding any per-template
    boost lists. ("compare batches" + operation="comparison" produces an
    embedding biased toward the comparison templates.)

    Returns the top-k templates above min_score (cosine). On any error
    (embed failure, no templates) returns []; the SQL pipeline then falls
    back to no-template generation.
    """
    if not query:
        return []

    await _ensure_template_embeddings(llm)
    if not _TEMPLATE_EMBEDDINGS:
        return []

    query_with_intent = query
    if operation:
        query_with_intent += f" (operation: {operation})"
    if aggregation:
        query_with_intent += f" (aggregation: {aggregation})"

    try:
        q_emb = np.array(await llm.embed(query_with_intent), dtype=np.float32)
    except Exception as exc:
        logger.warning("Template ranking: query embed failed: %s", exc)
        return []

    q_norm = q_emb / (np.linalg.norm(q_emb) + 1e-8)

    scored: list[tuple[float, dict]] = []
    for name, t_emb in _TEMPLATE_EMBEDDINGS.items():
        t_norm = t_emb / (np.linalg.norm(t_emb) + 1e-8)
        sim = float(q_norm @ t_norm)
        if sim >= min_score:
            scored.append((sim, _TEMPLATES[name]))

    scored.sort(key=lambda x: -x[0])
    return [t for _, t in scored[:top_k]]


def format_for_prompt(templates: list[dict]) -> str:
    """
    Render a list of templates as a markdown block for inclusion in the
    SQL-generation prompt. Empty list → empty string.
    """
    if not templates:
        return ""
    blocks = ["=== REFERENCE SQL TEMPLATES (consult — DO NOT copy blindly) ==="]
    for t in templates:
        blocks.append(
            f"\n### {t['name']}\n"
            f"WHEN: {t['when_to_use']}\n"
            f"```sql\n{t['sql'].strip()}\n```\n"
            f"NOTES: {t['notes']}"
        )
    blocks.append(
        "\nThese are reference shapes only. Write your SQL using the "
        "actual entities the user named, the schema, and the filters from "
        "the planner — adapt the template freely or write from scratch."
    )
    return "\n".join(blocks)

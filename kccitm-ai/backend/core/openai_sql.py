"""
OpenAI SQL fallback for complex queries.

When the local 7B model generates SQL that fails sanity checks,
this module asks OpenAI to generate correct SQL. Only the schema
and question are sent — NEVER student data.

Flow:
    1. Local model generates + executes SQL
    2. sanity_check() validates results against the question
    3. If check fails → generate_sql() asks OpenAI for correct SQL
    4. Execute OpenAI's SQL locally on our MySQL
"""

import logging
import re
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

# Compact schema sent to OpenAI (no student data)
_SCHEMA_TEXT = """
MySQL database 'kccitm' with 3 tables:

TABLE students (4,967 rows):
  roll_no VARCHAR(30) PK  -- first 2 digits = batch year (21=2021, 22=2022, 23=2023, 24=2024)
  name VARCHAR(255)       -- ALWAYS UPPERCASE
  course VARCHAR(100)     -- 'B.TECH', 'M.TECH'
  branch VARCHAR(300)     -- full name e.g. 'COMPUTER SCIENCE AND ENGINEERING'
  enrollment VARCHAR(50)
  father_name VARCHAR(255)
  gender VARCHAR(5)       -- 'M' or 'F'

TABLE semester_results (15,376 rows):
  id INT PK AUTO_INCREMENT
  roll_no VARCHAR(30) FK -> students.roll_no
  semester TINYINT        -- 1 to 8
  session VARCHAR(100)    -- e.g. '2021-22(REGULAR)'
  sgpa DECIMAL(4,2)       -- 0.00 to 10.00
  total_marks INT
  result_status VARCHAR(100) -- 'PASS', 'CP( 0)' (passed), 'CP( 1)' (1 backlog), 'PCP', 'PWG', 'FAIL'
  total_subjects TINYINT

TABLE subject_marks (172,168 rows):
  id INT PK AUTO_INCREMENT
  roll_no VARCHAR(30) FK -> students.roll_no
  semester TINYINT
  subject_code VARCHAR(30)
  subject_name VARCHAR(255)
  type VARCHAR(30)        -- 'Theory', 'Practical', 'CA'
  internal_marks SMALLINT
  external_marks SMALLINT
  grade VARCHAR(10)       -- 'A+','A','B+','B','C','D','E','F' (F = failed/back paper)
  back_paper VARCHAR(20)  -- '--' = no back paper, 'N*' = re-exam marks

CRITICAL RULES:
- Batch filter: WHERE roll_no LIKE '21%' for batch 2021 (use last 2 digits)
- Back papers / backlogs: use grade = 'F' in subject_marks (NOT back_paper != '--')
- Pass: result_status IN ('PASS','PCP','PWG') OR result_status LIKE 'CP(% 0)' OR result_status = 'CP(0)'
- Fail: result_status NOT in pass list
- CGPA: ROUND(AVG(sr.sgpa), 2) from semester_results
- Subject total marks: COALESCE(internal_marks,0) + COALESCE(external_marks,0)
- subject_name and subject_code ONLY exist in subject_marks, NOT in semester_results
- No window functions (OVER, PARTITION BY). Use subqueries instead.
- MySQL syntax only.
- CRITICAL for "passed all semesters" / "percentage who passed all":
  Use nested subquery: SELECT COUNT(*) FROM (SELECT roll_no FROM students s JOIN semester_results sr ... GROUP BY roll_no HAVING ...) t
  Do NOT use: SELECT COUNT(DISTINCT roll_no) ... GROUP BY roll_no HAVING ... (this returns one row per student, not a count)
  Correct pattern: SELECT ROUND(p.cnt * 100.0 / t.cnt, 2) FROM (SELECT COUNT(*) AS cnt FROM (SELECT s.roll_no FROM students s JOIN semester_results sr ON s.roll_no = sr.roll_no WHERE [gender_filter] GROUP BY s.roll_no HAVING SUM(CASE WHEN sr.result_status NOT IN ('PASS','PCP','PWG') AND sr.result_status NOT LIKE 'CP(%% 0)' AND sr.result_status != 'CP(0)' THEN 1 ELSE 0 END) = 0) inner_t) p, (SELECT COUNT(*) AS cnt FROM students WHERE [gender_filter]) t
"""


def sanity_check(question: str, sql: str, rows: list[dict]) -> tuple[bool, str]:
    """
    Quick validation: does the SQL result make sense for the question?
    Returns (is_valid, reason). Free — no API calls.
    """
    q = question.lower()
    sql_upper = sql.upper()

    # Check 1: Batch filter missing
    batch_match = re.search(r"batch\s*(\d{4})", q)
    if batch_match:
        batch_prefix = batch_match.group(1)[2:]  # 2023 → 23
        if f"LIKE '{batch_prefix}%" not in sql and f"LIKE '{batch_prefix}%" not in sql.replace("%%", "%"):
            # Verify by checking actual roll numbers in results
            if rows:
                wrong_batch = any(
                    not str(r.get("roll_no", "")).startswith(batch_prefix)
                    for r in rows[:5]
                    if r.get("roll_no")
                )
                if wrong_batch:
                    return False, f"Results contain wrong batch (expected {batch_prefix}xx)"

    # Check 2: back_paper != '--' used instead of grade = 'F'
    if any(w in q for w in ["back", "backlog", "fail", "failed", "year back"]):
        if "back_paper" in sql.lower() and "!= '--'" in sql:
            return False, "Used back_paper != '--' instead of grade = 'F'"

    # Check 3: Wrong table for subject queries
    subject_words = [
        "physics", "chemistry", "math", "dbms", "dsa", "operating system",
        "automata", "compiler", "network", "algorithm", "software",
        "subject", "marks in",
    ]
    if any(w in q for w in subject_words):
        if "subject_marks" not in sql.lower() and "semester_results" in sql.lower():
            if "sgpa" not in q and "cgpa" not in q:
                return False, "Subject query used semester_results instead of subject_marks"

    # Check 4: Impossible values
    for row in rows[:10]:
        if isinstance(row, dict):
            for key, val in row.items():
                if val is None:
                    continue
                try:
                    fval = float(val)
                    if "sgpa" in key.lower() and fval > 10:
                        return False, f"SGPA value {fval} > 10"
                    if "rate" in key.lower() or "percent" in key.lower():
                        if fval > 100:
                            return False, f"Percentage {fval} > 100%"
                except (ValueError, TypeError):
                    pass

    # Check 5: Percentage query returning many identical rows (broken GROUP BY pattern)
    if any(w in q for w in ["percentage", "percent", "rate", "proportion"]):
        if len(rows) > 5:
            # If a percentage query returns many rows with same value, it's broken
            first_vals = [list(r.values()) for r in rows[:5]]
            if first_vals and all(v == first_vals[0] for v in first_vals):
                return False, "Percentage query returned duplicate rows (broken GROUP BY)"

    # Check 6: LIMIT mismatch when user says "all"
    if any(w in q for w in ["all student", "every student", "list all", "all the student"]):
        limit_match = re.search(r"LIMIT\s+(\d+)", sql_upper)
        if limit_match and int(limit_match.group(1)) < 50:
            return False, f"User asked for 'all' but SQL has LIMIT {limit_match.group(1)}"

    return True, ""


async def generate_sql_via_openai(question: str) -> Optional[str]:
    """
    Ask OpenAI to generate SQL for a complex query.
    Sends ONLY the schema and question — never student data.
    Returns the SQL string or None.
    """
    if not settings.OPENAI_ENABLED or not settings.OPENAI_API_KEY:
        return None

    try:
        import httpx

        prompt = f"""{_SCHEMA_TEXT}

Generate a MySQL query to answer this question:
{question}

Rules:
- Return ONLY the SQL query, nothing else
- No markdown fences, no explanation
- Must be valid MySQL syntax
- Use LIMIT 200 if user asks for "all", LIMIT 10 for "top" without a number
- For back papers/backlogs, use grade = 'F' in subject_marks
- For batch filtering, use roll_no LIKE 'XX%' with last 2 digits of year
- ONLY add batch filter (roll_no LIKE) if user explicitly mentions a batch year. If no batch mentioned, query ALL students.
- Do NOT add extra filters the user didn't ask for"""

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.OPENAI_MODEL,
                    "max_tokens": 500,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a MySQL SQL generator. Return ONLY valid MySQL SELECT queries. No explanation, no markdown.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                },
            )

            if resp.status_code != 200:
                logger.warning("OpenAI SQL: HTTP %s", resp.status_code)
                return None

            content = resp.json()["choices"][0]["message"]["content"].strip()

            # Strip markdown fences if present
            content = re.sub(r"^```(?:sql)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
            content = content.strip().rstrip(";")

            if content.upper().startswith("SELECT"):
                logger.info("OpenAI SQL generated: %s", content[:80])
                return content

            # Try extracting SELECT from response
            match = re.search(r"(SELECT\s+.+)", content, re.IGNORECASE | re.DOTALL)
            if match:
                sql = match.group(1).rstrip(";").strip()
                logger.info("OpenAI SQL extracted: %s", sql[:80])
                return sql

            return None

    except Exception as exc:
        logger.warning("OpenAI SQL generation failed: %s", exc)
        return None

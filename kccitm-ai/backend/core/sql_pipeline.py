"""
Text-to-SQL pipeline for KCCITM AI Assistant.

Orchestrates: schema-aware prompt → LLM generates SQL → safety validation →
MySQL execution → formatted results (text + markdown table).

Usage:
    from core.llm_client import OllamaClient
    from core.sql_pipeline import SQLPipeline

    pipeline = SQLPipeline(OllamaClient())
    result = await pipeline.run("top 5 students by SGPA in semester 4", route_result)
    if result.success:
        print(result.formatted_table)
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field

import sqlparse

from config import settings
from core.llm_client import OllamaClient
from core.router import RouteResult
from db.mysql_client import execute_query

logger = logging.getLogger(__name__)

# ── SQL Generator System Prompt ───────────────────────────────────────────────

SQL_GENERATOR_SYSTEM_PROMPT = """You are a MySQL expert. Generate a SELECT query for any question about student academic data.

=== COMPLETE DATABASE SCHEMA ===

TABLE: students
| Column      | Type         | Description                    | Example values                           |
|-------------|--------------|--------------------------------|------------------------------------------|
| roll_no     | VARCHAR(20)  | Primary key, 13 digits         | '2104920100002'                          |
|             |              | First 2 digits = batch year    | 21=2021, 22=2022, 23=2023, 24=2024      |
| name        | VARCHAR(255) | Always UPPERCASE               | 'AAKASH SINGH', 'PRIYA SHARMA'          |
| course      | VARCHAR(100) | Degree program                 | 'B.TECH' (only value)                   |
| branch      | VARCHAR(200) | Full branch name, never abbrev | 'COMPUTER SCIENCE AND ENGINEERING'       |
|             |              |                                | 'ELECTRONICS AND COMMUNICATION ENGINEERING' |
|             |              |                                | 'MECHANICAL ENGINEERING'                 |
| enrollment  | VARCHAR(50)  | University enrollment number   | '2021049201002'                          |
| father_name | VARCHAR(255) | Father's name, UPPERCASE       | 'RAMESH SINGH'                           |
| gender      | CHAR(1)      | M or F                         | 'M', 'F'                                |

TABLE: semester_results
| Column         | Type         | Description                    | Example values                    |
|----------------|--------------|--------------------------------|-----------------------------------|
| id             | INT          | Auto-increment PK              |                                   |
| roll_no        | VARCHAR(20)  | FK -> students.roll_no         | '2104920100002'                   |
| semester       | INT          | 1 through 8                    | 1, 2, 3, 4, 5, 6, 7, 8          |
| session        | VARCHAR(100) | Academic session               | '2021-22 (REGULAR)', '2023-24 (REGULAR)' |
| sgpa           | DECIMAL(4,2) | 0.00 to 10.00                  | 8.45, 7.86, 0.00 (absent)        |
| total_marks    | INT          | Sum of all subject marks       | 719, 668, NULL                    |
| result_status  | VARCHAR(20)  | Pass/fail indicator            | 'PASS', 'CP(0)', 'CP( 0)', 'FAIL' |
|                |              | PASS = cleared all subjects    |                                   |
|                |              | CP(0) = compartment            |                                   |
| total_subjects | INT          | Number of subjects that sem    | 7, 8                              |

TABLE: subject_marks
| Column         | Type         | Description                    | Example values                    |
|----------------|--------------|--------------------------------|-----------------------------------|
| id             | INT          | Auto-increment PK              |                                   |
| roll_no        | VARCHAR(20)  | FK -> students.roll_no         | '2104920100002'                   |
| semester       | INT          | 1 through 8                    | 1, 2, 3                          |
| subject_code   | VARCHAR(20)  | University subject code        | 'KCS503', 'KAS101T', 'KCS301'   |
| subject_name   | VARCHAR(200) | Full subject name              | 'Database Management System'      |
|                |              |                                | 'Engineering Mathematics-I'       |
|                |              |                                | 'Data Structures'                 |
| type           | VARCHAR(20)  | Subject category               | 'Theory', 'Practical', 'CA'      |
| internal_marks | INT          | Sessional/internal (nullable)  | 28, 45, NULL                      |
| external_marks | INT          | University exam (nullable)     | 43, 70, NULL                      |
| grade          | VARCHAR(5)   | Letter grade                   | 'A+', 'A', 'B+', 'B', 'C', 'D', 'F', '' |
| back_paper     | VARCHAR(10)  | Back paper status              | '--' (no back paper), 'BACK'      |

=== RELATIONSHIPS ===
students.roll_no -> semester_results.roll_no (one student -> many semesters)
students.roll_no -> subject_marks.roll_no (one student -> many subjects)
semester_results and subject_marks share roll_no + semester

=== SAMPLE DATA (from real database) ===
students: ('2104920100002', 'AAKASH SINGH', 'B.TECH', 'COMPUTER SCIENCE AND ENGINEERING', 'M')
semester_results: ('2104920100002', 1, '2021-22 (REGULAR)', 8.45, 719, 'CP(0)', 8)
subject_marks: ('2104920100002', 1, 'KAS103T', 'Engineering Physics', 'Theory', 45, 70, 'A+', '--')

=== RULES ===
1. ONLY generate SELECT statements. Never INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE.
2. Use table aliases: s = students, sr = semester_results, sm = subject_marks.
3. Always add LIMIT. Use the number from the query ("top 5" = LIMIT 5). Default LIMIT 50.
4. Branch names are ALWAYS full: 'COMPUTER SCIENCE AND ENGINEERING' never 'CSE'.
   Map abbreviations: CSE, CS -> 'COMPUTER SCIENCE AND ENGINEERING'
   ECE -> 'ELECTRONICS AND COMMUNICATION ENGINEERING'
   ME -> 'MECHANICAL ENGINEERING'
5. Student names are UPPERCASE. Use LIKE for partial match: WHERE s.name LIKE '%%AAKASH%%'
6. There is NO batch_year column. Filter batch by roll_no prefix:
   Batch 2021 -> WHERE s.roll_no LIKE '21%%'
   Batch 2022 -> WHERE s.roll_no LIKE '22%%'
   Batch 2023 -> WHERE s.roll_no LIKE '23%%'
   Batch 2024 -> WHERE s.roll_no LIKE '24%%'
7. There is NO total_marks column in subject_marks. Calculate it:
   COALESCE(sm.internal_marks, 0) + COALESCE(sm.external_marks, 0) AS total
8. Pass/fail is NEVER determined by SGPA threshold. Use result_status:
   Passed: sr.result_status = 'PASS'
   Failed/compartment: sr.result_status LIKE 'CP%%' OR sr.result_status = 'FAIL'
9. Back paper: sm.back_paper != '--' means has back paper. sm.back_paper = '--' means clean.
10. Use ROUND(AVG(...), 2) for any average.
11. Use COALESCE() for nullable columns (internal_marks, external_marks, total_marks).
12. For subject search, use LIKE: WHERE sm.subject_name LIKE '%%Mathematics%%'
    Or exact code: WHERE sm.subject_code = 'KCS503'
13. When query asks for "all semesters" or "across semesters", do NOT filter by semester.
14. When joining all 3 tables, use:
    FROM students s
    JOIN semester_results sr ON s.roll_no = sr.roll_no
    JOIN subject_marks sm ON s.roll_no = sm.roll_no AND sr.semester = sm.semester
15. For gender analysis: s.gender = 'M' or s.gender = 'F'
16. For percentage calculations: ROUND(COUNT(condition) * 100.0 / COUNT(*), 2)
17. For comparisons between groups, use GROUP BY with the grouping column.

=== THINK STEP BY STEP ===
For any query:
1. Which tables do I need? (students only? + semester_results? + subject_marks?)
2. What JOINs? (always ON roll_no, add AND semester = semester for 3-table joins)
3. What WHERE filters? (semester, branch, name, subject, batch, gender, pass/fail, grade)
4. Do I need GROUP BY? (averages, counts per group, rankings by category)
5. What ORDER BY? (DESC for top/best/highest, ASC for bottom/worst/lowest)
6. What LIMIT? (match the query, default 50)

Respond with ONLY a JSON object:
{"sql": "SELECT ...", "params": [], "explanation": "one line explaining what this query does"}"""


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class SQLResult:
    """Result from the SQL pipeline."""

    success: bool
    sql: str = ""
    params: list = field(default_factory=list)
    explanation: str = ""
    rows: list[dict] = field(default_factory=list)
    row_count: int = 0
    formatted_text: str = ""
    formatted_table: str = ""
    error: str = ""
    execution_time_ms: float = 0.0


# ── Safety validator ──────────────────────────────────────────────────────────

class SQLValidator:
    """
    Validates generated SQL queries for safety before execution.
    All methods are static — no state needed.
    """

    FORBIDDEN_KEYWORDS = [
        "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
        "TRUNCATE", "REPLACE", "GRANT", "REVOKE", "EXEC", "EXECUTE",
        "CALL", "LOAD", "INTO OUTFILE", "INTO DUMPFILE", "INFORMATION_SCHEMA",
        "SLEEP", "BENCHMARK", "WAITFOR",
    ]

    MAX_JOINS = 3
    MAX_SUBQUERIES = 2
    MAX_RESULT_LIMIT = 100

    @staticmethod
    def validate(sql: str) -> str | None:
        """
        Validate SQL query for safety.
        Returns None if safe, or an error message string if unsafe.
        """
        if not sql or not sql.strip():
            return "Empty SQL query"

        sql_upper = sql.upper().strip()

        # 1. Must be SELECT or WITH (CTE)
        if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")):
            return f"Only SELECT statements allowed. Got: {sql_upper[:20]}..."

        # 2. Forbidden keywords (word-boundary match to avoid false positives)
        for keyword in SQLValidator.FORBIDDEN_KEYWORDS:
            pattern = r"\b" + re.escape(keyword) + r"\b"
            if re.search(pattern, sql_upper):
                return f"Forbidden keyword detected: {keyword}"

        # 3. No SQL comments (-- preceded by whitespace/start, or block comments)
        # Allow '--' inside string literals like back_paper = '--'
        if re.search(r"(?:^|\s)--", sql) or "/*" in sql:
            return "SQL comments not allowed"

        # 4. No multiple statements
        statements = sqlparse.split(sql)
        if len([s for s in statements if s.strip()]) > 1:
            return "Multiple SQL statements not allowed"

        # 5. JOIN count
        join_count = len(re.findall(r"\bJOIN\b", sql_upper))
        if join_count > SQLValidator.MAX_JOINS:
            return f"Too many JOINs: {join_count} (max {SQLValidator.MAX_JOINS})"

        # 6. Subquery count (additional SELECTs beyond the main one)
        subquery_count = sql_upper.count("SELECT") - 1
        if subquery_count > SQLValidator.MAX_SUBQUERIES:
            return f"Too many subqueries: {subquery_count} (max {SQLValidator.MAX_SUBQUERIES})"

        # 7. LIMIT value
        limit_match = re.search(r"LIMIT\s+(\d+)", sql_upper)
        if limit_match:
            limit_val = int(limit_match.group(1))
            if limit_val > SQLValidator.MAX_RESULT_LIMIT:
                return f"LIMIT too high: {limit_val} (max {SQLValidator.MAX_RESULT_LIMIT})"

        return None  # All checks passed

    @staticmethod
    def enforce_limit(sql: str, max_limit: int = 100) -> str:
        """Add or reduce LIMIT clause to enforce max_limit."""
        limit_match = re.search(r"LIMIT\s+(\d+)", sql, flags=re.IGNORECASE)
        if limit_match:
            current = int(limit_match.group(1))
            if current > max_limit:
                sql = re.sub(
                    r"LIMIT\s+\d+", f"LIMIT {max_limit}", sql, flags=re.IGNORECASE
                )
        else:
            sql = sql.rstrip(";").strip() + f" LIMIT {max_limit}"
        return sql


# ── Pipeline ──────────────────────────────────────────────────────────────────

class SQLPipeline:
    """
    Converts natural language queries to SQL, validates, executes, and formats results.

    Pipeline:
        1. Build a schema-aware prompt with the user's question + router context
        2. LLM generates SQL + explanation as JSON
        3. Safety layer validates (SELECT only, no injection, etc.)
        4. Execute against MySQL with row limit enforcement
        5. Format results as readable text + markdown table
    """

    def __init__(self, llm: OllamaClient) -> None:
        self.llm = llm
        self.max_rows = 100
        self.query_timeout = 5

    async def search_student(self, identifier: str) -> dict:
        """
        Search for a student and return full results.
        identifier can be: name (partial), roll_no (exact 13 digits), or batch year (4 digits).
        """
        # Step 1: Find matching students
        if identifier.isdigit() and len(identifier) == 13:
            students = await execute_query(
                "SELECT * FROM students WHERE roll_no = %s", (identifier,)
            )
        elif identifier.isdigit() and len(identifier) <= 4:
            prefix = identifier[2:4] if len(identifier) == 4 else identifier
            students = await execute_query(
                "SELECT * FROM students WHERE roll_no LIKE %s LIMIT 50",
                (f"{prefix}%",),
            )
        else:
            students = await execute_query(
                "SELECT * FROM students WHERE name LIKE %s LIMIT 10",
                (f"%{identifier.upper()}%",),
            )

        if not students:
            return {"found": 0, "students": [], "detail": None}

        if len(students) > 1:
            return {"found": len(students), "students": students, "detail": None}

        # Single student found — get full details
        student = students[0]
        roll = student["roll_no"]

        semesters = await execute_query(
            "SELECT semester, session, sgpa, total_marks, result_status, total_subjects "
            "FROM semester_results WHERE roll_no = %s ORDER BY semester",
            (roll,),
        )

        subjects = await execute_query(
            "SELECT semester, subject_code, subject_name, type, internal_marks, "
            "external_marks, grade, back_paper "
            "FROM subject_marks WHERE roll_no = %s ORDER BY semester, subject_code",
            (roll,),
        )

        return {
            "found": 1,
            "students": [student],
            "detail": {
                "student": student,
                "semesters": semesters,
                "subjects": subjects,
            },
        }

    async def run(self, query: str, route_result: RouteResult) -> SQLResult:
        """
        Full pipeline: NL question → SQL → validate → execute → format.

        Never raises — all error paths return SQLResult(success=False, error="...").
        """
        # Step 1: Generate SQL
        generated = await self._generate_sql(query, route_result)
        if not generated.success:
            return generated

        # Step 2: Validate
        validation_error = self._validate_sql(generated.sql)
        if validation_error:
            return SQLResult(
                success=False,
                sql=generated.sql,
                error=f"SQL validation failed: {validation_error}",
            )

        # Step 3: Enforce LIMIT and execute
        result = await self._execute_sql(generated)
        if not result.success:
            return result

        # Step 4: Format
        result.formatted_text = self._format_as_text(
            result.rows, result.sql, generated.explanation
        )
        result.formatted_table = self._format_as_markdown_table(result.rows)
        return result

    # ── Internal steps ────────────────────────────────────────────────────────

    async def _generate_sql(self, query: str, route_result: RouteResult) -> SQLResult:
        """Use LLM to generate SQL from natural language."""
        system_prompt = await self._get_system_prompt()
        user_prompt = self._build_user_prompt(query, route_result)

        try:
            response = await self.llm.generate(
                prompt=user_prompt,
                system=system_prompt,
                temperature=0.05,
                max_tokens=800,
                format="json",
                options={"temperature": 0.05},
            )
            return self._parse_sql_response(response)
        except Exception as exc:
            return SQLResult(success=False, error=f"SQL generation failed: {exc}")

    async def _get_system_prompt(self) -> str:
        """Load SQL generator prompt from prompts.db, fallback to hardcoded."""
        try:
            from db.sqlite_client import fetch_one
            from config import settings
            row = await fetch_one(
                settings.PROMPTS_DB,
                "SELECT content FROM prompt_templates "
                "WHERE prompt_name = ? AND section_name = ? AND is_active = 1",
                ("sql_generator", "system"),
            )
            if row and row.get("content"):
                return row["content"]
        except Exception as exc:
            logger.warning("Could not load sql_generator prompt from DB: %s", exc)
        return SQL_GENERATOR_SYSTEM_PROMPT

    def _build_user_prompt(self, query: str, route_result: RouteResult) -> str:
        """
        Build user prompt combining the question with router-extracted context.
        """
        parts = [f"Question: {query}"]

        if route_result.intent:
            parts.append(f"Intent: {route_result.intent}")

        if route_result.filters:
            filter_strs = [
                f"{k}={v}"
                for k, v in route_result.filters.items()
                if v is not None
            ]
            if filter_strs:
                parts.append(f"Filters: {', '.join(filter_strs)}")

        if route_result.entities:
            parts.append(f"Entities: {', '.join(str(e) for e in route_result.entities)}")

        return "\n".join(parts)

    def _parse_sql_response(self, response: str) -> SQLResult:
        """
        Parse LLM JSON response into SQLResult.

        Handles: markdown fences, missing keys, malformed JSON.
        Falls back to regex SQL extraction on complete parse failure.
        """
        text = self._clean_json(response)

        try:
            data = json.loads(text)
            sql = str(data.get("sql", "") or "").strip()
            if not sql:
                return SQLResult(success=False, error="LLM returned empty SQL")
            sql = self._strip_sql_comments(sql)
            return SQLResult(
                success=True,
                sql=sql,
                params=list(data.get("params") or []),
                explanation=str(data.get("explanation") or "").strip(),
            )
        except json.JSONDecodeError:
            # Try to extract SQL via regex
            match = re.search(
                r"(SELECT\s+.+?)(?:;|\Z)", text, re.IGNORECASE | re.DOTALL
            )
            if match:
                return SQLResult(
                    success=True,
                    sql=self._strip_sql_comments(match.group(1).strip()),
                    explanation="(extracted from raw response)",
                )
            return SQLResult(
                success=False,
                error=f"Cannot parse LLM response: {text[:200]}",
            )

    def _validate_sql(self, sql: str) -> str | None:
        return SQLValidator.validate(sql)

    async def _execute_sql(self, generated: SQLResult) -> SQLResult:
        """Execute validated SQL, enforcing LIMIT."""
        sql = SQLValidator.enforce_limit(generated.sql, self.max_rows)
        params = tuple(generated.params) if generated.params else ()
        # Escape literal % signs (e.g. LIKE 'CP%') that are not %s placeholders.
        # aiomysql interprets bare % as format specifiers; %% → % after formatting.
        sql = re.sub(r"%(?!s\b)", "%%", sql)

        try:
            t0 = time.perf_counter()
            rows = await execute_query(sql, params)
            elapsed_ms = (time.perf_counter() - t0) * 1000

            return SQLResult(
                success=True,
                sql=generated.sql,
                params=generated.params,
                explanation=generated.explanation,
                rows=rows,
                row_count=len(rows),
                execution_time_ms=elapsed_ms,
            )
        except Exception as exc:
            return SQLResult(
                success=False,
                sql=generated.sql,
                params=generated.params,
                explanation=generated.explanation,
                error=f"SQL execution error: {exc}",
            )

    # ── Formatters ────────────────────────────────────────────────────────────

    def _format_as_text(
        self, rows: list[dict], sql: str, explanation: str
    ) -> str:
        """Format SQL results as human-readable text for LLM context."""
        if not rows:
            return "Query returned 0 rows. No matching data found."

        lines: list[str] = [f"Query returned {len(rows)} row(s)."]
        if explanation:
            lines.append(f"Explanation: {explanation}")
        lines.append("")
        lines.append("Results:")

        display_rows = rows[:20]
        for i, row in enumerate(display_rows, 1):
            parts = []
            for k, v in row.items():
                label = self._clean_column_name(k)
                if isinstance(v, float):
                    v = round(v, 2)
                parts.append(f"{label}: {v}")
            lines.append(f"  {i}. {' | '.join(parts)}")

        if len(rows) > 20:
            lines.append(f"  ... and {len(rows) - 20} more rows")

        return "\n".join(lines)

    def _format_as_markdown_table(self, rows: list[dict]) -> str:
        """Format SQL results as a markdown table for frontend display."""
        if not rows:
            return ""

        display_rows = rows[:25]
        columns = list(display_rows[0].keys())
        headers = [self._clean_column_name(c) for c in columns]

        # Header row
        header_line = "| " + " | ".join(headers) + " |"
        separator = "| " + " | ".join("---" for _ in headers) + " |"

        data_lines: list[str] = []
        for row in display_rows:
            cells = []
            for col in columns:
                val = row.get(col, "")
                if isinstance(val, float):
                    val = round(val, 2)
                cell = str(val) if val is not None else ""
                if len(cell) > 40:
                    cell = cell[:37] + "..."
                cells.append(cell)
            data_lines.append("| " + " | ".join(cells) + " |")

        return "\n".join([header_line, separator] + data_lines)

    @staticmethod
    def _clean_column_name(col: str) -> str:
        """Convert snake_case or lowercase column name to Title Case."""
        return col.replace("_", " ").title()

    @staticmethod
    def _strip_sql_comments(sql: str) -> str:
        """Remove SQL comments while preserving -- inside string literals."""
        # Remove block comments /* ... */
        sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
        # Remove inline comments — only -- preceded by whitespace or at line start
        # This avoids stripping '--' inside string literals like back_paper != '--'
        sql = re.sub(r"(?:^|\s)--[^\n]*", " ", sql, flags=re.MULTILINE)
        # Collapse extra whitespace
        sql = re.sub(r"[ \t]+", " ", sql).strip()
        return sql

    @staticmethod
    def _clean_json(text: str) -> str:
        """Strip markdown code fences from LLM output."""
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        return text.strip()


# ── Prompt update helper ──────────────────────────────────────────────────────

async def update_sql_generator_prompt() -> None:
    """
    Replace the Phase 2 placeholder with the real SQL generator prompt in prompts.db.
    Safe to call multiple times.
    """
    from db.sqlite_client import execute
    await execute(
        settings.PROMPTS_DB,
        "UPDATE prompt_templates SET content = ?, version = 1 "
        "WHERE prompt_name = 'sql_generator' AND section_name = 'system'",
        (SQL_GENERATOR_SYSTEM_PROMPT,),
    )
    logger.info("Updated sql_generator/system prompt in prompts.db")

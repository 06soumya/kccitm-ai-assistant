"""
Text-to-SQL pipeline for KCCITM AI Assistant.

BIRD-bench inspired upgrades:
  1. Schema linking — only include relevant tables/columns in the prompt
  2. Self-correction — retry with error context on failure (max 2 retries)
  3. Execution verification — sanity-check results before returning
  4. Chain-of-thought — force step-by-step reasoning before SQL generation
  5. Database value hints — POSSIBLE VALUES for every enum-like column

Orchestrates: schema linking → CoT prompt → LLM generates SQL → safety validation →
MySQL execution → self-correction loop → verification → formatted results.

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
from typing import Optional

import sqlparse

from config import settings
from core.llm_client import OllamaClient
from core.router import RouteResult
from db.mysql_client import execute_query

logger = logging.getLogger(__name__)


# ── UPGRADE 1: Schema Linking ────────────────────────────────────────────────

# Full schema definitions per table — used by schema_link() to select relevant parts
SCHEMA_TABLES = {
    "students": {
        "alias": "s",
        "columns": {
            "roll_no":     {"type": "VARCHAR(20)", "desc": "Primary key, 13 digits. First 2 digits = batch year (21=2021, 22=2022, 23=2023, 24=2024)"},
            "name":        {"type": "VARCHAR(255)", "desc": "Always UPPERCASE"},
            "course":      {"type": "VARCHAR(100)", "desc": "Degree program. POSSIBLE VALUES: 'B.TECH'"},
            "branch":      {"type": "VARCHAR(200)", "desc": "Full branch name, never abbreviated. POSSIBLE VALUES: 'COMPUTER SCIENCE AND ENGINEERING', 'ELECTRONICS AND COMMUNICATION ENGINEERING', 'MECHANICAL ENGINEERING'"},
            "enrollment":  {"type": "VARCHAR(50)", "desc": "University enrollment number"},
            "father_name": {"type": "VARCHAR(255)", "desc": "Father's name, UPPERCASE"},
            "gender":      {"type": "CHAR(1)", "desc": "POSSIBLE VALUES: 'M', 'F'"},
        },
    },
    "semester_results": {
        "alias": "sr",
        "columns": {
            "id":              {"type": "INT", "desc": "Auto-increment PK"},
            "roll_no":         {"type": "VARCHAR(20)", "desc": "FK -> students.roll_no"},
            "semester":        {"type": "INT", "desc": "POSSIBLE VALUES: 1, 2, 3, 4, 5, 6, 7, 8"},
            "session":         {"type": "VARCHAR(100)", "desc": "Academic session, e.g. '2021-22 (REGULAR)'"},
            "sgpa":            {"type": "DECIMAL(4,2)", "desc": "0.00 to 10.00. 0.00 = absent/no data"},
            "total_marks":     {"type": "INT", "desc": "Sum of all subject marks that semester (nullable)"},
            "result_status":   {"type": "VARCHAR(20)", "desc": "POSSIBLE VALUES: 'PASS', 'CP(0)', 'CP( 0)', 'FAIL'. PASS=cleared all. CP=compartment"},
            "total_subjects":  {"type": "INT", "desc": "Number of subjects that semester"},
        },
    },
    "subject_marks": {
        "alias": "sm",
        "columns": {
            "id":              {"type": "INT", "desc": "Auto-increment PK"},
            "roll_no":         {"type": "VARCHAR(20)", "desc": "FK -> students.roll_no"},
            "semester":        {"type": "INT", "desc": "POSSIBLE VALUES: 1, 2, 3, 4, 5, 6, 7, 8"},
            "subject_code":    {"type": "VARCHAR(20)", "desc": "University subject code, e.g. 'KCS503', 'KAS101T'"},
            "subject_name":    {"type": "VARCHAR(200)", "desc": "Full subject name, e.g. 'Database Management System'"},
            "type":            {"type": "VARCHAR(20)", "desc": "POSSIBLE VALUES: 'Theory', 'Practical', 'CA'"},
            "internal_marks":  {"type": "INT", "desc": "Sessional/internal marks (nullable). Use COALESCE(sm.internal_marks, 0)"},
            "external_marks":  {"type": "INT", "desc": "University exam marks (nullable). Use COALESCE(sm.external_marks, 0)"},
            "grade":           {"type": "VARCHAR(5)", "desc": "POSSIBLE VALUES: 'A+', 'A', 'B+', 'B', 'C', 'D', 'F', '' (empty)"},
            "back_paper":      {"type": "VARCHAR(10)", "desc": "POSSIBLE VALUES: '--' (no back paper), 'BACK' (has back paper)"},
        },
    },
}

# Keyword → (table, [columns]) mapping for schema linking
_SCHEMA_LINK_RULES: list[tuple[list[str], str, list[str]]] = [
    # (keywords, table, columns_to_include)
    (["sgpa", "gpa", "cgpa"],                          "semester_results", ["roll_no", "semester", "sgpa", "result_status"]),
    (["marks", "score", "total marks", "scored"],      "subject_marks",    ["roll_no", "semester", "subject_name", "internal_marks", "external_marks", "grade"]),
    (["marks", "score", "total marks"],                "semester_results", ["roll_no", "semester", "total_marks"]),
    (["branch", "cse", "ece", "mechanical", "department"], "students",    ["roll_no", "name", "branch"]),
    (["fail", "pass", "passed", "failed", "result", "compartment", "cp"], "semester_results", ["roll_no", "semester", "result_status", "sgpa"]),
    (["subject", "code", "course name"],               "subject_marks",    ["roll_no", "semester", "subject_code", "subject_name", "type"]),
    (["grade", "letter grade"],                         "subject_marks",    ["roll_no", "semester", "subject_name", "grade"]),
    (["back paper", "back", "backlog"],                 "subject_marks",    ["roll_no", "semester", "subject_name", "back_paper"]),
    (["gender", "male", "female", "boy", "girl"],       "students",        ["roll_no", "name", "gender"]),
    (["batch", "year", "2021", "2022", "2023", "2024"], "students",        ["roll_no", "name", "branch"]),
    (["name", "student name", "called"],                "students",        ["roll_no", "name"]),
    (["semester", "sem"],                               "semester_results", ["roll_no", "semester", "sgpa", "total_marks", "result_status"]),
    (["topper", "top", "highest", "best", "rank"],      "semester_results", ["roll_no", "semester", "sgpa", "total_marks"]),
    (["average", "avg", "mean"],                        "semester_results", ["roll_no", "semester", "sgpa", "total_marks"]),
    (["percentage", "percent", "%"],                    "semester_results", ["roll_no", "semester", "result_status"]),
    (["improve", "improvement", "progress", "trend"],   "semester_results", ["roll_no", "semester", "sgpa"]),
    (["compare", "comparison", "vs", "between"],        "students",        ["roll_no", "name", "branch"]),
    (["internal", "sessional", "external", "university exam"], "subject_marks", ["roll_no", "semester", "subject_name", "internal_marks", "external_marks"]),
    (["session", "regular", "back"],                    "semester_results", ["roll_no", "semester", "session"]),
    (["enrollment", "enroll"],                          "students",        ["roll_no", "name", "enrollment"]),
    (["father"],                                        "students",        ["roll_no", "name", "father_name"]),
]


def schema_link(query: str) -> dict:
    """
    UPGRADE 1: Identify which tables and columns are relevant to the query.

    Returns:
        {
            "tables": {"students": ["roll_no", "name", ...], "semester_results": [...], ...},
            "join_hint": str  # suggested JOIN pattern
        }
    """
    q_lower = query.lower()
    tables: dict[str, set[str]] = {}

    for keywords, table, columns in _SCHEMA_LINK_RULES:
        for kw in keywords:
            if kw in q_lower:
                if table not in tables:
                    tables[table] = set()
                tables[table].update(columns)
                break  # one keyword match per rule is enough

    # Always include students table for name/roll context
    if "students" not in tables:
        tables["students"] = {"roll_no", "name", "branch"}

    # If no semester_results or subject_marks matched, include semester_results as default
    if "semester_results" not in tables and "subject_marks" not in tables:
        tables["semester_results"] = {"roll_no", "semester", "sgpa", "total_marks", "result_status"}

    # Build join hint
    table_names = set(tables.keys())
    if table_names == {"students"}:
        join_hint = "FROM students s"
    elif table_names == {"students", "semester_results"}:
        join_hint = "FROM students s JOIN semester_results sr ON s.roll_no = sr.roll_no"
    elif table_names == {"students", "subject_marks"}:
        join_hint = "FROM students s JOIN subject_marks sm ON s.roll_no = sm.roll_no"
    elif len(table_names) == 3 or ("semester_results" in table_names and "subject_marks" in table_names):
        tables.setdefault("students", set()).add("roll_no")
        join_hint = (
            "FROM students s "
            "JOIN semester_results sr ON s.roll_no = sr.roll_no "
            "JOIN subject_marks sm ON s.roll_no = sm.roll_no AND sr.semester = sm.semester"
        )
    else:
        join_hint = "FROM students s JOIN semester_results sr ON s.roll_no = sr.roll_no"

    return {
        "tables": {t: sorted(cols) for t, cols in tables.items()},
        "join_hint": join_hint,
    }


def _build_linked_schema(linked: dict) -> str:
    """Format schema_link() output into a prompt-friendly schema description."""
    lines = []
    for table_name, columns in linked["tables"].items():
        table_def = SCHEMA_TABLES[table_name]
        alias = table_def["alias"]
        lines.append(f"\nTABLE: {table_name} (alias: {alias})")
        lines.append(f"{'Column':<20} {'Type':<15} Description")
        lines.append("-" * 80)
        for col_name in columns:
            col = table_def["columns"].get(col_name, {})
            lines.append(f"{col_name:<20} {col.get('type', '?'):<15} {col.get('desc', '')}")
    lines.append(f"\nSuggested JOIN: {linked['join_hint']}")
    return "\n".join(lines)


# ── UPGRADE 4 + 5: Chain-of-Thought System Prompt with Value Hints ───────────

SQL_GENERATOR_SYSTEM_PROMPT = """You are a MySQL expert. Generate a SELECT query for any question about student academic data.

=== DATABASE SCHEMA (only relevant tables shown) ===
{linked_schema}

=== RELATIONSHIPS ===
students.roll_no -> semester_results.roll_no (one student -> many semesters)
students.roll_no -> subject_marks.roll_no (one student -> many subjects)
semester_results and subject_marks share roll_no + semester

=== SAMPLE DATA ===
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
    For conditional counting use SUM(CASE WHEN condition THEN 1 ELSE 0 END).
17. For comparisons between groups, use GROUP BY with the grouping column.
18. SGPA can NEVER exceed 10.0. If you see values > 10, something is wrong.
19. For "students who passed ALL semesters", use:
    GROUP BY s.roll_no HAVING SUM(CASE WHEN sr.result_status != 'PASS' THEN 1 ELSE 0 END) = 0
20. For "SGPA improved every semester", compare each semester's SGPA with the previous one.

=== CHAIN OF THOUGHT (you MUST follow this) ===
Before writing SQL, think through these steps:
Step 1: What tables do I need? List them.
Step 2: What columns does the user want to see? List them.
Step 3: What are the filter conditions? List them.
Step 4: Do I need GROUP BY? Why?
Step 5: What ORDER BY and LIMIT?
Step 6: Now write the SQL.

Respond with ONLY a JSON object in this exact format:
{{"thinking": "Step 1: ... Step 2: ... Step 3: ... Step 4: ... Step 5: ... Step 6: ...", "sql": "SELECT ...", "explanation": "one line explaining what this query does"}}"""


# Retry prompt template for self-correction (UPGRADE 2)
SQL_RETRY_PROMPT = """The previous SQL query failed.

Previous SQL: {previous_sql}
Error: {error_message}

Fix the SQL to resolve this error. Follow the same schema and rules.
Common fixes:
- Unknown column: check the schema above for correct column names
- Syntax error: check MySQL syntax rules
- No results: try relaxing WHERE conditions

Respond with ONLY a JSON object:
{{"thinking": "The error was... I need to fix...", "sql": "SELECT ...", "explanation": "fixed query"}}"""


# Zero-result retry prompt (UPGRADE 2)
SQL_ZERO_ROWS_PROMPT = """The previous query returned 0 rows, but the user clearly expects results.

Previous SQL: {previous_sql}
User question: {original_query}

The query may have been too restrictive. Try:
- Removing or relaxing one WHERE condition
- Using LIKE instead of exact match
- Checking if the column values are correct (see POSSIBLE VALUES in schema)

Respond with ONLY a JSON object:
{{"thinking": "The query returned no results because... I'll relax...", "sql": "SELECT ...", "explanation": "relaxed query"}}"""


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
    retries_used: int = 0
    schema_linked: dict = field(default_factory=dict)
    verification_warnings: list[str] = field(default_factory=list)


# ── Safety validator ─────────────────────────────────────────────────────────

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


# ── UPGRADE 3: Execution Verification ────────────────────────────────────────

def verify_results(query: str, sql: str, rows: list[dict]) -> list[str]:
    """
    Sanity-check SQL results against the original natural language query.
    Returns a list of warning strings (empty = all good).
    """
    warnings = []
    q_lower = query.lower()
    sql_upper = sql.upper()

    # Check: "how many" expects a single count row
    if ("how many" in q_lower or "count" in q_lower) and len(rows) > 1:
        if "COUNT" not in sql_upper:
            warnings.append("Query asks 'how many' but SQL has no COUNT(*). Results may show raw rows instead of a count.")

    # Check: "average" / "avg" expects AVG in SQL
    if ("average" in q_lower or "avg" in q_lower) and "AVG" not in sql_upper:
        warnings.append("Query asks for average but SQL has no AVG(). Results may show raw values.")

    # Check: SGPA > 10.0 is impossible
    for row in rows[:20]:
        for key, val in row.items():
            if "sgpa" in key.lower() and isinstance(val, (int, float)) and val > 10.0:
                warnings.append(f"SGPA value {val} exceeds maximum 10.0 — possible data error or incorrect aggregation.")
                break
        else:
            continue
        break

    # Check: too many rows without explicit "all" in query
    if len(rows) > 50 and "all" not in q_lower and "every" not in q_lower:
        warnings.append(f"Query returned {len(rows)} rows. Consider adding LIMIT if not all rows are needed.")

    return warnings


# ── Pipeline ─────────────────────────────────────────────────────────────────

class SQLPipeline:
    """
    Converts natural language queries to SQL, validates, executes, and formats results.

    BIRD-bench inspired pipeline:
        1. Schema linking — identify relevant tables/columns
        2. Chain-of-thought prompt with value hints
        3. LLM generates SQL with step-by-step reasoning
        4. Safety validation (SELECT only, no injection, etc.)
        5. Execute against MySQL
        6. Self-correction — retry on error or zero rows (max 2 retries)
        7. Execution verification — sanity-check results
        8. Format results as text + markdown table
    """

    MAX_RETRIES = 2

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
        Full BIRD-style pipeline: schema link → CoT generate → validate → execute →
        self-correct → verify → format.

        Never raises — all error paths return SQLResult(success=False, error="...").
        """
        # UPGRADE 1: Schema linking
        linked = schema_link(query)
        linked_schema_text = _build_linked_schema(linked)

        # Step 1: Generate SQL with chain-of-thought
        generated = await self._generate_sql(query, route_result, linked_schema_text)
        if not generated.success:
            return generated
        generated.schema_linked = linked

        # Step 2: Validate
        validation_error = self._validate_sql(generated.sql)
        if validation_error:
            return SQLResult(
                success=False,
                sql=generated.sql,
                error=f"SQL validation failed: {validation_error}",
                schema_linked=linked,
            )

        # Step 3: Execute with self-correction loop (UPGRADE 2)
        result = await self._execute_with_retries(
            generated, query, route_result, linked_schema_text
        )
        if not result.success:
            return result
        result.schema_linked = linked

        # Step 4: Execution verification (UPGRADE 3)
        result.verification_warnings = verify_results(query, result.sql, result.rows)
        if result.verification_warnings:
            logger.info(
                "Verification warnings for '%s': %s",
                query[:50], result.verification_warnings,
            )

        # Step 5: Format
        result.formatted_text = self._format_as_text(
            result.rows, result.sql, generated.explanation, result.verification_warnings
        )
        result.formatted_table = self._format_as_markdown_table(result.rows)
        return result

    # ── Internal steps ────────────────────────────────────────────────────────

    async def _generate_sql(
        self,
        query: str,
        route_result: RouteResult,
        linked_schema_text: str,
        error_context: Optional[str] = None,
        previous_sql: Optional[str] = None,
        temperature: float = 0.05,
    ) -> SQLResult:
        """Use LLM to generate SQL from natural language with chain-of-thought."""
        system_prompt = await self._get_system_prompt(linked_schema_text)

        if error_context and previous_sql:
            # Self-correction retry prompt
            user_prompt = SQL_RETRY_PROMPT.format(
                previous_sql=previous_sql, error_message=error_context
            )
        else:
            user_prompt = self._build_user_prompt(query, route_result)

        try:
            response = await self.llm.generate(
                prompt=user_prompt,
                system=system_prompt,
                temperature=temperature,
                max_tokens=1000,
                format="json",
            )
            return self._parse_sql_response(response)
        except Exception as exc:
            return SQLResult(success=False, error=f"SQL generation failed: {exc}")

    async def _generate_sql_zero_rows(
        self,
        query: str,
        previous_sql: str,
        linked_schema_text: str,
    ) -> SQLResult:
        """Generate a relaxed SQL query after zero rows."""
        system_prompt = await self._get_system_prompt(linked_schema_text)
        user_prompt = SQL_ZERO_ROWS_PROMPT.format(
            previous_sql=previous_sql, original_query=query
        )

        try:
            response = await self.llm.generate(
                prompt=user_prompt,
                system=system_prompt,
                temperature=0.01,
                max_tokens=1000,
                format="json",
            )
            return self._parse_sql_response(response)
        except Exception as exc:
            return SQLResult(success=False, error=f"SQL retry generation failed: {exc}")

    async def _get_system_prompt(self, linked_schema_text: str) -> str:
        """Build system prompt with linked schema injected."""
        # Try loading from prompts.db first
        try:
            from db.sqlite_client import fetch_one
            row = await fetch_one(
                settings.PROMPTS_DB,
                "SELECT content FROM prompt_templates "
                "WHERE prompt_name = ? AND section_name = ? AND is_active = 1",
                ("sql_generator", "system"),
            )
            if row and row.get("content"):
                template = row["content"]
                if "{linked_schema}" in template:
                    return template.format(linked_schema=linked_schema_text)
                return template
        except Exception as exc:
            logger.warning("Could not load sql_generator prompt from DB: %s", exc)

        return SQL_GENERATOR_SYSTEM_PROMPT.format(linked_schema=linked_schema_text)

    def _build_user_prompt(self, query: str, route_result: RouteResult) -> str:
        """Build user prompt combining the question with router-extracted context."""
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
        Handles chain-of-thought "thinking" field, markdown fences, missing keys.
        """
        text = self._clean_json(response)

        try:
            data = json.loads(text)
            sql = str(data.get("sql", "") or "").strip()
            if not sql:
                return SQLResult(success=False, error="LLM returned empty SQL")
            sql = self._strip_sql_comments(sql)

            thinking = str(data.get("thinking", "") or "").strip()
            explanation = str(data.get("explanation", "") or "").strip()
            if thinking:
                logger.debug("CoT thinking: %s", thinking[:200])

            return SQLResult(
                success=True,
                sql=sql,
                params=list(data.get("params") or []),
                explanation=explanation,
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

    async def _execute_with_retries(
        self,
        generated: SQLResult,
        query: str,
        route_result: RouteResult,
        linked_schema_text: str,
    ) -> SQLResult:
        """
        UPGRADE 2: Execute SQL with self-correction on error or zero rows.

        Retry strategy:
          - On execution error: regenerate with error message at lower temperature
          - On zero rows (when results expected): regenerate with relaxed filters
          - Max 2 retries total
        """
        current = generated
        retries = 0

        while retries <= self.MAX_RETRIES:
            result = await self._execute_sql(current)

            if result.success and result.row_count > 0:
                result.retries_used = retries
                return result

            if not result.success and retries < self.MAX_RETRIES:
                # Execution error — retry with error context
                logger.info(
                    "SQL execution failed (retry %d/%d): %s",
                    retries + 1, self.MAX_RETRIES, result.error[:100],
                )
                current = await self._generate_sql(
                    query, route_result, linked_schema_text,
                    error_context=result.error,
                    previous_sql=current.sql,
                    temperature=0.01,
                )
                if not current.success:
                    return current

                validation_error = self._validate_sql(current.sql)
                if validation_error:
                    return SQLResult(
                        success=False,
                        sql=current.sql,
                        error=f"Retry SQL validation failed: {validation_error}",
                    )
                retries += 1
                continue

            if result.success and result.row_count == 0 and retries < self.MAX_RETRIES:
                # Zero rows — check if query clearly expects results
                if self._expects_results(query):
                    logger.info(
                        "SQL returned 0 rows, query expects results (retry %d/%d)",
                        retries + 1, self.MAX_RETRIES,
                    )
                    current = await self._generate_sql_zero_rows(
                        query, current.sql, linked_schema_text
                    )
                    if not current.success:
                        return current

                    validation_error = self._validate_sql(current.sql)
                    if validation_error:
                        return SQLResult(
                            success=False,
                            sql=current.sql,
                            error=f"Retry SQL validation failed: {validation_error}",
                        )
                    retries += 1
                    continue
                else:
                    result.retries_used = retries
                    return result

            # Out of retries or non-retryable
            result.retries_used = retries
            return result

        return result

    @staticmethod
    def _expects_results(query: str) -> bool:
        """Heuristic: does this query clearly expect non-empty results?"""
        q = query.lower()
        # Questions that could legitimately return 0 rows
        if any(w in q for w in ["is there", "does", "any", "exist"]):
            return False
        # Questions that definitely expect results
        if any(w in q for w in ["top", "best", "highest", "lowest", "show", "list",
                                 "find", "get", "which", "who", "compare", "what"]):
            return True
        return False

    async def _execute_sql(self, generated: SQLResult) -> SQLResult:
        """Execute validated SQL, enforcing LIMIT."""
        sql = SQLValidator.enforce_limit(generated.sql, self.max_rows)
        params = tuple(generated.params) if generated.params else ()
        # Escape literal % signs (e.g. LIKE 'CP%') that are not %s placeholders.
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
        self, rows: list[dict], sql: str, explanation: str,
        warnings: list[str] = None,
    ) -> str:
        """Format SQL results as human-readable text for LLM context."""
        if not rows:
            return "Query returned 0 rows. No matching data found."

        lines: list[str] = [f"Query returned {len(rows)} row(s)."]
        if explanation:
            lines.append(f"Explanation: {explanation}")

        if warnings:
            for w in warnings:
                lines.append(f"Warning: {w}")

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
        sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
        sql = re.sub(r"(?:^|\s)--[^\n]*", " ", sql, flags=re.MULTILINE)
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


# ── Prompt update helper ─────────────────────────────────────────────────────

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

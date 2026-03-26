"""
ETL Pipeline: MySQL university_marks JSON → normalized SQL tables.

Reads every row from university_marks, parses the jsontext blob, and
populates three normalized tables:
  - students           (one row per student)
  - semester_results   (one row per semester per student)
  - subject_marks      (one row per subject per semester per student)

Usage:
    cd backend
    python -m ingestion.etl

Idempotent: tables are dropped and recreated on each run.
"""

import json
import logging
import re
import sys
from typing import Any

import pymysql

from config import settings
from db.mysql_client import get_sync_connection, sync_execute

# ── ANSI colours ─────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

BATCH_SIZE = 500

# ── DDL ───────────────────────────────────────────────────────────────────────

_DDL_STUDENTS = """
CREATE TABLE IF NOT EXISTS students (
    roll_no        VARCHAR(30)  PRIMARY KEY,
    name           VARCHAR(255) NOT NULL,
    course         VARCHAR(100),
    branch         VARCHAR(300),
    enrollment     VARCHAR(50),
    father_name    VARCHAR(255),
    gender         VARCHAR(5)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_DDL_SEMESTER_RESULTS = """
CREATE TABLE IF NOT EXISTS semester_results (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    roll_no        VARCHAR(30)  NOT NULL,
    semester       TINYINT      NOT NULL,
    session        VARCHAR(100),
    sgpa           DECIMAL(4,2),
    total_marks    INT,
    result_status  VARCHAR(100),
    total_subjects TINYINT,
    UNIQUE KEY uq_roll_sem (roll_no, semester),
    FOREIGN KEY (roll_no) REFERENCES students(roll_no) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_DDL_SUBJECT_MARKS = """
CREATE TABLE IF NOT EXISTS subject_marks (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    roll_no         VARCHAR(30)  NOT NULL,
    semester        TINYINT      NOT NULL,
    subject_code    VARCHAR(30),
    subject_name    VARCHAR(255),
    type            VARCHAR(30),
    internal_marks  SMALLINT,
    external_marks  SMALLINT,
    grade           VARCHAR(10),
    back_paper      VARCHAR(20),
    FOREIGN KEY (roll_no) REFERENCES students(roll_no) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_prefix(field: str) -> str:
    """Remove leading code prefix like '(04) ' from course/branch strings."""
    if not field:
        return field
    return re.sub(r'^\(\d+\)\s*', '', field).strip()


def _safe_int(value: Any, default: int | None = None) -> int | None:
    """Convert value to int, returning default on failure."""
    try:
        return int(value) if value not in (None, "", "--") else default
    except (ValueError, TypeError):
        return default


def _safe_float(value: Any, default: float | None = None) -> float | None:
    """Convert value to float, returning default on failure."""
    try:
        v = float(value) if value not in (None, "", "--") else default
        return v if v and v > 0 else default
    except (ValueError, TypeError):
        return default


def _is_empty_semester(sem: dict) -> bool:
    """Return True if the semester entry should be skipped."""
    return (
        not str(sem.get("semester", "")).strip()
        or str(sem.get("total_subjects", "0")).strip() == "0"
        or str(sem.get("semester", "")).strip() == ""
        or _safe_float(sem.get("SGPA", "0")) is None
    )


def _setup_tables(conn: pymysql.Connection) -> None:
    """Drop (if exists) and recreate the three normalized tables."""
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS subject_marks")
        cur.execute("DROP TABLE IF EXISTS semester_results")
        cur.execute("DROP TABLE IF EXISTS students")
        cur.execute(_DDL_STUDENTS)
        cur.execute(_DDL_SEMESTER_RESULTS)
        cur.execute(_DDL_SUBJECT_MARKS)
    conn.commit()
    print(f"{GREEN}✓ Tables created: students, semester_results, subject_marks{RESET}")


def _load_raw_rows(conn: pymysql.Connection) -> list[dict]:
    """Fetch all rows from university_marks."""
    with conn.cursor() as cur:
        cur.execute("SELECT roll_no, jsontext FROM university_marks")
        return list(cur.fetchall())


def _parse_student(data: dict) -> dict:
    """Extract student-level fields from parsed JSON."""
    return {
        "roll_no":    str(data.get("rollno", "")).strip(),
        "name":       str(data.get("name", "")).strip().upper(),
        "course":     _strip_prefix(data.get("course", "")),
        "branch":     _strip_prefix(data.get("branch", "")),
        "enrollment": str(data.get("enrollment", "")).strip(),
        "father_name": str(data.get("fname", "")).strip(),
        "gender":     str(data.get("gender", "")).strip(),
    }


def _parse_semesters(roll_no: str, data: dict) -> list[dict]:
    """Extract semester-level records, skipping empty entries."""
    records = []
    for sem in data.get("result", []):
        if _is_empty_semester(sem):
            continue
        records.append({
            "roll_no":        roll_no,
            "semester":       int(str(sem.get("semester", "0")).strip()),
            "session":        str(sem.get("session", "")).replace("Session : ", "").strip(),
            "sgpa":           _safe_float(sem.get("SGPA")),
            "total_marks":    _safe_int(sem.get("total_marks_obt")),
            "result_status":  str(sem.get("result_status", "")).strip(),
            "total_subjects": _safe_int(sem.get("total_subjects")),
        })
    return records


def _parse_subjects(roll_no: str, data: dict) -> list[dict]:
    """Extract subject-level records across all valid semesters."""
    records = []
    for sem in data.get("result", []):
        if _is_empty_semester(sem):
            continue
        sem_no = int(str(sem.get("semester", "0")).strip())
        for subj in sem.get("marks", []):
            internal = _safe_int(subj.get("internal"))
            external_raw = str(subj.get("external", "")).strip()
            external = _safe_int(external_raw) if external_raw not in ("", "--") else None
            records.append({
                "roll_no":       roll_no,
                "semester":      sem_no,
                "subject_code":  str(subj.get("code", "")).strip(),
                "subject_name":  str(subj.get("name", "")).strip(),
                "type":          str(subj.get("type", "")).strip(),
                "internal_marks": internal,
                "external_marks": external,
                "grade":         str(subj.get("grade", "")).strip(),
                "back_paper":    str(subj.get("back_paper", "")).strip(),
            })
    return records


def _batch_insert_students(conn: pymysql.Connection, batch: list[dict]) -> None:
    sql = """
        INSERT IGNORE INTO students
            (roll_no, name, course, branch, enrollment, father_name, gender)
        VALUES
            (%(roll_no)s, %(name)s, %(course)s, %(branch)s,
             %(enrollment)s, %(father_name)s, %(gender)s)
    """
    with conn.cursor() as cur:
        cur.executemany(sql, batch)
    conn.commit()


def _batch_insert_semesters(conn: pymysql.Connection, batch: list[dict]) -> None:
    sql = """
        INSERT IGNORE INTO semester_results
            (roll_no, semester, session, sgpa, total_marks, result_status, total_subjects)
        VALUES
            (%(roll_no)s, %(semester)s, %(session)s, %(sgpa)s,
             %(total_marks)s, %(result_status)s, %(total_subjects)s)
    """
    with conn.cursor() as cur:
        cur.executemany(sql, batch)
    conn.commit()


def _batch_insert_subjects(conn: pymysql.Connection, batch: list[dict]) -> None:
    sql = """
        INSERT INTO subject_marks
            (roll_no, semester, subject_code, subject_name, type,
             internal_marks, external_marks, grade, back_paper)
        VALUES
            (%(roll_no)s, %(semester)s, %(subject_code)s, %(subject_name)s, %(type)s,
             %(internal_marks)s, %(external_marks)s, %(grade)s, %(back_paper)s)
    """
    with conn.cursor() as cur:
        cur.executemany(sql, batch)
    conn.commit()


# ── Main ETL ──────────────────────────────────────────────────────────────────

def run_etl() -> None:
    """Run the complete ETL pipeline."""
    conn = get_sync_connection()
    try:
        # Step 1: Setup tables
        _setup_tables(conn)

        # Step 2: Load raw rows
        print("Loading rows from university_marks...")
        raw_rows = _load_raw_rows(conn)
        total = len(raw_rows)
        print(f"Found {total} rows to process.")

        # Step 3: Parse and accumulate
        students_batch: list[dict] = []
        semesters_batch: list[dict] = []
        subjects_batch: list[dict] = []

        students_count = 0
        semester_count = 0
        subject_count = 0
        error_count = 0

        for idx, row in enumerate(raw_rows, 1):
            if idx % 100 == 0 or idx == total:
                print(f"  Processing student {idx}/{total}...", end="\r", flush=True)

            try:
                data = json.loads(row["jsontext"])
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("Bad JSON for roll_no=%s: %s", row.get("roll_no"), exc)
                error_count += 1
                continue

            student = _parse_student(data)
            if not student["roll_no"]:
                continue

            students_batch.append(student)
            semesters_batch.extend(_parse_semesters(student["roll_no"], data))
            subjects_batch.extend(_parse_subjects(student["roll_no"], data))

            # Flush in batches — always students first (FK parent before children)
            if len(students_batch) >= BATCH_SIZE:
                _batch_insert_students(conn, students_batch)
                students_count += len(students_batch)
                students_batch = []

                _batch_insert_semesters(conn, semesters_batch)
                semester_count += len(semesters_batch)
                semesters_batch = []

                _batch_insert_subjects(conn, subjects_batch)
                subject_count += len(subjects_batch)
                subjects_batch = []

        # Flush remaining
        print()  # newline after \r progress
        if students_batch:
            _batch_insert_students(conn, students_batch)
            students_count += len(students_batch)
        if semesters_batch:
            _batch_insert_semesters(conn, semesters_batch)
            semester_count += len(semesters_batch)
        if subjects_batch:
            _batch_insert_subjects(conn, subjects_batch)
            subject_count += len(subjects_batch)

        if error_count:
            print(f"{YELLOW}⚠ Skipped {error_count} rows with bad JSON{RESET}")

        print(
            f"\n{GREEN}✓ ETL complete. "
            f"{students_count} students, "
            f"{semester_count} semester records, "
            f"{subject_count} subject marks.{RESET}"
        )

    finally:
        conn.close()


if __name__ == "__main__":
    run_etl()

"""
Text Chunk Generator for KCCITM AI Assistant.

Reads from university_marks table and generates one natural language
text chunk per student per semester.

Chunk format example:
    Student AAKASH SINGH (Roll: 2104920100002), B.TECH Computer Science and
    Engineering, Semester 1, Session 2021-22 (REGULAR). SGPA: 8.45, Total
    Marks: 719. Result: CP(0).
    Theory subjects: Engineering Physics B+ (45+70=115), ...
    Practical subjects: Engineering Physics Lab A+ (23+23=46), ...

Usage:
    cd backend
    python -m ingestion.chunker
"""

import json
import logging
import os
import re
from typing import Any

from config import settings
from db.mysql_client import get_sync_connection

# ── ANSI colours ─────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = "data"
CHUNKS_FILE = os.path.join(DATA_DIR, "chunks.jsonl")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_prefix(field: str) -> str:
    """Remove leading code prefix like '(04) ' from course/branch strings."""
    if not field:
        return ""
    return re.sub(r'^\(\d+\)\s*', '', field).strip()


def _clean_session(raw: str) -> str:
    """
    Clean session string.
    'Session : 2021-22(REGULAR)' → '2021-22 (REGULAR)'
    """
    s = raw.replace("Session : ", "").strip()
    # Insert space before parenthesis if missing: "2021-22(REGULAR)" → "2021-22 (REGULAR)"
    s = re.sub(r'(\S)\(', r'\1 (', s)
    return s


def _clean_result_status(raw: str) -> str:
    """Strip extra whitespace from result_status: 'CP( 0)' → 'CP(0)'"""
    return re.sub(r'\(\s+', '(', raw.strip())


def _is_empty_semester(sem: dict) -> bool:
    """Return True if the semester should be skipped."""
    sem_str = str(sem.get("semester", "")).strip()
    total_str = str(sem.get("total_subjects", "0")).strip()
    sgpa_str = str(sem.get("SGPA", "0")).strip()
    return (
        not sem_str
        or total_str == "0"
        or not sgpa_str
        or sgpa_str == "0"
    )


def _format_subject_entry(subj: dict) -> str:
    """
    Format a single subject as 'Name Grade (internal+external=total)'.
    Handles missing external marks (practicals like 'Mini Project').
    """
    name = str(subj.get("name", "")).strip()
    grade = str(subj.get("grade", "")).strip()
    try:
        internal = int(subj.get("internal", "0") or "0")
    except (ValueError, TypeError):
        internal = 0

    external_raw = str(subj.get("external", "")).strip()
    if external_raw in ("", "--", "0"):
        # No external marks (project / CA)
        total = internal
        marks_str = f"({internal})"
    else:
        try:
            external = int(external_raw)
        except (ValueError, TypeError):
            external = 0
        total = internal + external
        marks_str = f"({internal}+{external}={total})"

    grade_part = f" {grade}" if grade else ""
    return f"{name}{grade_part} {marks_str}"


def _build_chunk_text(
    name: str,
    roll_no: str,
    course: str,
    branch: str,
    sem_no: int,
    session: str,
    sgpa: float,
    total_marks: str,
    result_status: str,
    marks: list[dict],
) -> str:
    """Assemble the full natural language chunk text for one student-semester."""
    # Header line
    header = (
        f"Student {name} (Roll: {roll_no}), {course} {branch.title()}, "
        f"Semester {sem_no}, Session {session}. "
        f"SGPA: {sgpa}, Total Marks: {total_marks}. Result: {result_status}."
    )

    # Group subjects by type
    theory: list[str] = []
    practical: list[str] = []
    other: list[str] = []

    for subj in marks:
        entry = _format_subject_entry(subj)
        subj_type = str(subj.get("type", "")).strip().lower()
        if "theory" in subj_type:
            theory.append(entry)
        elif "practical" in subj_type or "lab" in subj_type:
            practical.append(entry)
        else:
            other.append(entry)

    lines = [header]
    if theory:
        lines.append("Theory subjects: " + ", ".join(theory) + ".")
    if practical:
        lines.append("Practical subjects: " + ", ".join(practical) + ".")
    if other:
        lines.append("Other subjects: " + ", ".join(other) + ".")

    return "\n".join(lines)


def _build_metadata(
    roll_no: str,
    name: str,
    branch: str,
    course: str,
    sem_no: int,
    sgpa: float,
    session: str,
    result_status: str,
    gender: str,
) -> dict:
    """Build the metadata dict for a chunk."""
    return {
        "chunk_id":      f"{roll_no}_sem{sem_no}",
        "roll_no":       roll_no,
        "name":          name,
        "branch":        branch,
        "course":        course,
        "semester":      sem_no,
        "sgpa":          sgpa,
        "session":       session,
        "result_status": result_status,
        "gender":        gender,
    }


# ── Core generator ────────────────────────────────────────────────────────────

def generate_chunks() -> list[tuple[str, dict]]:
    """
    Generate all text chunks from the university_marks table.

    Returns:
        List of (chunk_text, metadata_dict) tuples.
        One tuple per valid student-semester.
    """
    conn = get_sync_connection()
    chunks: list[tuple[str, dict]] = []
    student_count = 0
    error_count = 0

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT roll_no, jsontext FROM university_marks")
            rows = list(cur.fetchall())
    finally:
        conn.close()

    total = len(rows)
    print(f"Loaded {total} rows. Generating chunks...")

    for idx, row in enumerate(rows, 1):
        if idx % 200 == 0 or idx == total:
            print(f"  Processing {idx}/{total}...", end="\r", flush=True)

        try:
            data = json.loads(row["jsontext"])
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("Bad JSON row %d: %s", idx, exc)
            error_count += 1
            continue

        roll_no  = str(data.get("rollno", "")).strip()
        name     = str(data.get("name", "")).strip().upper()
        course   = _strip_prefix(data.get("course", ""))
        branch   = _strip_prefix(data.get("branch", ""))
        gender   = str(data.get("gender", "")).strip()

        if not roll_no:
            continue

        student_count += 1

        for sem in data.get("result", []):
            if _is_empty_semester(sem):
                continue

            sem_no  = int(str(sem.get("semester", "0")).strip())
            session = _clean_session(str(sem.get("session", "")))
            _sgpa_raw = sem.get("SGPA", 0)
            try:
                sgpa = float(_sgpa_raw or 0)
            except (ValueError, TypeError):
                sgpa = 0.0
            total_marks = str(sem.get("total_marks_obt", "")).strip()
            result_status = _clean_result_status(str(sem.get("result_status", "")))
            marks = sem.get("marks", [])

            chunk_text = _build_chunk_text(
                name=name,
                roll_no=roll_no,
                course=course,
                branch=branch,
                sem_no=sem_no,
                session=session,
                sgpa=sgpa,
                total_marks=total_marks,
                result_status=result_status,
                marks=marks,
            )

            metadata = _build_metadata(
                roll_no=roll_no,
                name=name,
                branch=branch,
                course=course,
                sem_no=sem_no,
                sgpa=sgpa,
                session=session,
                result_status=result_status,
                gender=gender,
            )

            chunks.append((chunk_text, metadata))

    print()  # newline after \r progress

    if error_count:
        print(f"{YELLOW}⚠ Skipped {error_count} bad JSON rows{RESET}")

    return chunks


def save_chunks(chunks: list[tuple[str, dict]]) -> None:
    """Save chunks to data/chunks.jsonl for debugging and reuse."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CHUNKS_FILE, "w", encoding="utf-8") as f:
        for text, meta in chunks:
            f.write(json.dumps({"text": text, "metadata": meta}, ensure_ascii=False) + "\n")
    print(f"{GREEN}✓ Saved {len(chunks)} chunks to {CHUNKS_FILE}{RESET}")


def load_chunks_from_file() -> list[tuple[str, dict]]:
    """Load chunks from data/chunks.jsonl (faster than re-parsing MySQL)."""
    chunks = []
    with open(CHUNKS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            obj = json.loads(line)
            chunks.append((obj["text"], obj["metadata"]))
    return chunks


if __name__ == "__main__":
    chunks = generate_chunks()
    student_set = {m["roll_no"] for _, m in chunks}
    print(f"{GREEN}✓ Generated {len(chunks)} chunks for {len(student_set)} students{RESET}")
    save_chunks(chunks)

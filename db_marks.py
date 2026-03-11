import os
from dotenv import load_dotenv
import mysql.connector
import pandas as pd
import json
import re

load_dotenv()


def safe_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def get_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME")
    )


def derive_batch_from_roll(roll_no):
    roll_no = str(roll_no).strip()
    if len(roll_no) >= 2 and roll_no[:2].isdigit():
        return 2000 + int(roll_no[:2])
    return None


def calculate_subject_total(internal, external):
    internal_num = safe_float(internal)
    external_num = safe_float(external)

    if internal_num is None and external_num is None:
        return None

    return (internal_num or 0) + (external_num or 0)


def normalize_name(name):
    return re.sub(r"\s+", " ", str(name).strip().upper())


def get_marks(roll_no):
    connection = None
    cursor = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT jsontext
            FROM university_marks
            WHERE roll_no = %s
            LIMIT 1
            """,
            (roll_no,)
        )

        result = cursor.fetchone()

        if not result:
            return None, None, None

        raw_json = result[0]
        data = json.loads(raw_json)

        student_info = {
            "Name": data.get("name", ""),
            "Roll Number": data.get("rollno", ""),
            "Enrollment": data.get("enrollment", ""),
            "Course": data.get("course", ""),
            "Branch": data.get("branch", ""),
            "Father Name": data.get("fname", ""),
            "Gender": data.get("gender", ""),
            "Batch": derive_batch_from_roll(data.get("rollno", ""))
        }

        student_df = pd.DataFrame(
            [{"Field": key, "Value": value} for key, value in student_info.items()]
        )

        semester_rows = []
        subject_rows = []

        for semester in data.get("result", []):
            sem = str(semester.get("semester", "")).strip()

            if not sem:
                continue

            semester_rows.append({
                "Semester": sem,
                "Session": semester.get("session", ""),
                "Result Status": semester.get("result_status", ""),
                "Total Marks": semester.get("total_marks_obt", ""),
                "SGPA": semester.get("SGPA", "")
            })

            for subject in semester.get("marks", []):
                subject_rows.append({
                    "Semester": sem,
                    "Session": semester.get("session", ""),
                    "Subject Code": str(subject.get("code", "")).strip(),
                    "Subject Name": str(subject.get("name", "")).strip(),
                    "Type": subject.get("type", ""),
                    "Internal": subject.get("internal", ""),
                    "External": subject.get("external", ""),
                    "Grade": subject.get("grade", "")
                })

        semester_df = pd.DataFrame(semester_rows)
        subject_df = pd.DataFrame(subject_rows)

        return student_df, semester_df, subject_df

    except Exception as e:
        error_df = pd.DataFrame([{"Field": "Error", "Value": str(e)}])
        return error_df, None, None

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def search_students_by_name(name_query, batch_year=None, top_n=50):
    connection = None
    cursor = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT roll_no, jsontext
            FROM university_marks
            """
        )

        rows = cursor.fetchall()

        target_name = normalize_name(name_query)
        target_tokens = [tok for tok in target_name.split() if tok]

        results = []

        for roll_no, raw_json in rows:
            try:
                data = json.loads(raw_json)
            except Exception:
                continue

            student_name = data.get("name", "")
            normalized_student_name = normalize_name(student_name)
            student_tokens = normalized_student_name.split()
            student_batch = derive_batch_from_roll(data.get("rollno", roll_no))

            exact_match = normalized_student_name == target_name
            contains_match = target_name in normalized_student_name
            common_tokens = [tok for tok in target_tokens if tok in student_tokens]
            token_match_count = len(common_tokens)

            if batch_year is not None and student_batch != batch_year:
                continue

            if not exact_match and not contains_match and token_match_count < 2:
                continue

            score = 0
            if exact_match:
                score += 100
            elif contains_match:
                score += 80
            else:
                score += token_match_count * 20

            results.append({
                "Name": student_name,
                "Roll Number": str(data.get("rollno", roll_no)),
                "Batch": student_batch,
                "Course": data.get("course", ""),
                "Branch": data.get("branch", ""),
                "Match Score": score,
                "Match Type": (
                    "Exact" if exact_match else
                    "Contains" if contains_match else
                    f"{token_match_count} Token Match"
                )
            })

        if not results:
            return pd.DataFrame()

        df = pd.DataFrame(results).drop_duplicates(subset=["Roll Number"]).reset_index(drop=True)
        df = df.sort_values(
            by=["Match Score", "Name", "Roll Number"],
            ascending=[False, True, True]
        ).reset_index(drop=True)

        df.insert(0, "Sequence", range(1, len(df) + 1))
        return df.head(top_n)

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def get_best_or_weakest_subject(subject_df, mode="best"):
    if subject_df is None or subject_df.empty:
        return None

    temp = subject_df.copy()
    temp["Total"] = temp.apply(
        lambda row: calculate_subject_total(row.get("Internal"), row.get("External")),
        axis=1
    )
    temp = temp.dropna(subset=["Total"])

    if temp.empty:
        return None

    idx = temp["Total"].idxmax() if mode == "best" else temp["Total"].idxmin()
    return temp.loc[[idx]].drop(columns=["Total"])


def calculate_average_marks(subject_df):
    if subject_df is None or subject_df.empty:
        return None

    temp = subject_df.copy()
    temp["Total"] = temp.apply(
        lambda row: calculate_subject_total(row.get("Internal"), row.get("External")),
        axis=1
    )
    temp = temp.dropna(subset=["Total"])

    if temp.empty:
        return None

    return round(float(temp["Total"].mean()), 2)


def calculate_percentage(subject_df):
    if subject_df is None or subject_df.empty:
        return None

    temp = subject_df.copy()
    temp["Total"] = temp.apply(
        lambda row: calculate_subject_total(row.get("Internal"), row.get("External")),
        axis=1
    )
    temp = temp.dropna(subset=["Total"])

    if temp.empty:
        return None

    total_scored = float(temp["Total"].sum())
    max_total = len(temp) * 100

    if max_total == 0:
        return None

    return round((total_scored / max_total) * 100, 2)


def get_subject_toppers(batch_year, semester_no, subject_query, top_n=10):
    connection = None
    cursor = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        batch_prefix = str(batch_year)[2:]

        cursor.execute(
            """
            SELECT roll_no, jsontext
            FROM university_marks
            WHERE CAST(roll_no AS CHAR) LIKE %s
            """,
            (f"{batch_prefix}%",)
        )

        rows = cursor.fetchall()
        topper_rows = []
        subject_query_lower = str(subject_query).strip().lower()

        for roll_no, raw_json in rows:
            try:
                data = json.loads(raw_json)
            except Exception:
                continue

            student_name = data.get("name", "")
            roll_no_str = str(data.get("rollno", roll_no)).strip()

            for semester in data.get("result", []):
                sem = str(semester.get("semester", "")).strip()
                if sem != str(semester_no).strip():
                    continue

                sgpa = semester.get("SGPA", "")

                for subject in semester.get("marks", []):
                    subject_code = str(subject.get("code", "")).strip()
                    subject_name = str(subject.get("name", "")).strip()

                    if not (
                        subject_query_lower in subject_code.lower()
                        or subject_query_lower in subject_name.lower()
                    ):
                        continue

                    total_marks = calculate_subject_total(
                        subject.get("internal", ""),
                        subject.get("external", "")
                    )

                    if total_marks is None:
                        continue

                    topper_rows.append({
                        "Name": student_name,
                        "Roll Number": roll_no_str,
                        "Batch": batch_year,
                        "Semester": sem,
                        "Subject Code": subject_code,
                        "Subject Name": subject_name,
                        "Subject Marks": total_marks,
                        "SGPA": sgpa
                    })

        if not topper_rows:
            return pd.DataFrame()

        df = pd.DataFrame(topper_rows)
        df["Subject Marks"] = pd.to_numeric(df["Subject Marks"], errors="coerce")
        df["SGPA_num"] = pd.to_numeric(df["SGPA"], errors="coerce")

        df = df.sort_values(
            by=["Subject Marks", "SGPA_num", "Name"],
            ascending=[False, False, True]
        ).reset_index(drop=True)

        df.insert(0, "Rank", range(1, len(df) + 1))
        df = df.drop(columns=["SGPA_num"])

        return df.head(top_n)

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def get_batch_toppers_by_cgpa(batch_year, top_n=10):
    connection = None
    cursor = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        batch_prefix = str(batch_year)[2:]

        cursor.execute(
            """
            SELECT roll_no, jsontext
            FROM university_marks
            WHERE CAST(roll_no AS CHAR) LIKE %s
            """,
            (f"{batch_prefix}%",)
        )

        rows = cursor.fetchall()
        result_rows = []

        for roll_no, raw_json in rows:
            try:
                data = json.loads(raw_json)
            except Exception:
                continue

            sgpas = []

            for semester in data.get("result", []):
                sem = str(semester.get("semester", "")).strip()
                if not sem:
                    continue

                sgpa_val = safe_float(semester.get("SGPA", ""))
                if sgpa_val is not None and sgpa_val > 0:
                    sgpas.append(sgpa_val)

            if not sgpas:
                continue

            derived_cgpa = round(sum(sgpas) / len(sgpas), 2)

            result_rows.append({
                "Name": data.get("name", ""),
                "Roll Number": str(data.get("rollno", roll_no)),
                "Batch": batch_year,
                "Derived CGPA": derived_cgpa
            })

        if not result_rows:
            return pd.DataFrame()

        df = pd.DataFrame(result_rows)
        df["Derived CGPA"] = pd.to_numeric(df["Derived CGPA"], errors="coerce")
        df = df.sort_values(
            by=["Derived CGPA", "Name"],
            ascending=[False, True]
        ).reset_index(drop=True)

        df.insert(0, "Rank", range(1, len(df) + 1))
        return df.head(top_n)

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()
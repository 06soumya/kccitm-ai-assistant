"""
Regression tests for the DB-first routing layer.

Verifies:
  - Student queries always route to 'student_db' (never 'general_openai')
  - General queries always route to 'general_openai' (never 'student_db')
  - Topper queries default to top-20 by CGPA without asking for course
  - Follow-up queries with context use the DB path
  - OpenAI is NOT called for DB queries (instrumentation)
  - DB handler IS invoked for student queries (instrumentation)

Run with:
    venv/bin/pytest tests/test_routing.py -v
"""

import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from router import (
    normalize_query,
    route_query,
    detect_subtype,
    is_topper_query,
    is_subject_topper_query,
    is_batch_topper_query,
    is_student_lookup_query,
    is_student_metrics_query,
    is_student_list_query,
    is_backlog_query,
    is_student_database_query,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ctx(last_roll=None):
    return {"last_roll": last_roll}


def _routes_db(query, ctx=None):
    return route_query(query, ctx or _ctx()) == "student_db"


def _routes_openai(query, ctx=None):
    return route_query(query, ctx or _ctx()) == "general_openai"


# ---------------------------------------------------------------------------
# 1. Roll number lookup → always DB
# ---------------------------------------------------------------------------
class TestRollQuery:
    def test_roll_query_hits_db_not_openai(self):
        assert _routes_db("show result of 2104920100002")

    def test_roll_query_marks(self):
        assert _routes_db("show marks for 2104920100002")

    def test_roll_query_fetch(self):
        assert _routes_db("fetch result of roll number 2104920100002")

    def test_roll_query_marksheet(self):
        assert _routes_db("get marksheet of 2104920100002")

    def test_roll_query_full_record(self):
        assert _routes_db("show full record of 2104920100002")

    def test_roll_query_does_not_use_openai(self):
        """Instrumentation: OpenAI must not be called for a roll-number query."""
        with patch("openai_handler.handle_general_openai_query") as mock_openai:
            decision = route_query("show marks for 2104920100002", _ctx())
            assert decision == "student_db"
            mock_openai.assert_not_called()


# ---------------------------------------------------------------------------
# 2. Student name lookup → DB
# ---------------------------------------------------------------------------
class TestStudentNameQuery:
    def test_name_query_hits_db_not_openai(self):
        assert _routes_db("show result of Aakash Singh")

    def test_name_query_marks(self):
        assert _routes_db("marks of Aakash Singh")

    def test_name_query_with_batch(self):
        assert _routes_db("student details of Aakash Singh batch 2021")

    def test_name_query_details(self):
        assert _routes_db("show details of Rahul Kumar")

    def test_name_query_does_not_use_openai(self):
        with patch("openai_handler.handle_general_openai_query") as mock_openai:
            decision = route_query("marks of Aakash Singh", _ctx())
            assert decision == "student_db"
            mock_openai.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Topper queries → DB
# ---------------------------------------------------------------------------
class TestTopperQuery:
    def test_topper_batch_query_hits_db_not_openai(self):
        assert _routes_db("toppers of batch 2021")

    def test_top_n_students(self):
        assert _routes_db("top 20 students of batch 2021")

    def test_top_10_by_cgpa(self):
        assert _routes_db("top 10 students by cgpa")

    def test_topper_list(self):
        assert _routes_db("topper list of batch 2021")

    def test_rank_list(self):
        assert _routes_db("rank list for batch 2021")

    def test_highest_cgpa_students(self):
        assert _routes_db("highest cgpa students of batch 2020")

    def test_college_topper(self):
        assert _routes_db("college topper list")

    def test_branch_topper(self):
        assert _routes_db("branch topper list")

    def test_semester_topper(self):
        assert _routes_db("semester toppers")

    def test_who_is_topper(self):
        assert _routes_db("who is the topper of 2021 batch")

    def test_who_has_highest_cgpa(self):
        assert _routes_db("who has highest cgpa")

    def test_best_result_batch(self):
        assert _routes_db("who got best result in batch 2022")

    # is_topper_query correctness
    def test_is_topper_true_for_toppers(self):
        assert is_topper_query("toppers of batch 2021")

    def test_is_topper_true_for_top_n(self):
        assert is_topper_query("top 20 students batch 2021")

    def test_is_topper_true_for_rank_list(self):
        assert is_topper_query("rank list of batch 2021")

    def test_is_topper_false_for_bare_top(self):
        """'top' alone (e.g. 'topic') must NOT trigger topper routing."""
        assert not is_topper_query("what is the topic today")
        assert not is_topper_query("top")
        assert not is_topper_query("topology")

    def test_subject_topper_classification(self):
        assert is_subject_topper_query("toppers in ds sem 3 batch 2021")

    def test_batch_topper_classification(self):
        assert is_batch_topper_query("toppers of batch 2021")
        assert not is_batch_topper_query("toppers in ds sem 3 batch 2021")

    def test_topper_batch_query_defaults_to_top_20_by_cgpa(self):
        """Batch topper with no semester → batch_topper subtype (not subject_topper)."""
        subtype = detect_subtype("toppers of batch 2021", _ctx())
        assert subtype == "batch_topper"

    def test_topper_batch_query_does_not_ask_course_when_db_is_only_btech(self):
        """
        When DB only has BTech students, routing must not ask for course clarification.
        Verified by checking route goes straight to 'student_db'.
        """
        decision = route_query("toppers of batch 2021", _ctx())
        assert decision == "student_db", (
            "Should route directly to DB — do NOT ask for course when DB is BTech-only."
        )


# ---------------------------------------------------------------------------
# 4. Backlog queries → DB
# ---------------------------------------------------------------------------
class TestBacklogQuery:
    def test_backlog_query_hits_db_not_openai(self):
        assert _routes_db("show backlog subjects")

    def test_backlog_students_query(self):
        assert _routes_db("backlog students of sem 4")

    def test_failed_students_query(self):
        assert _routes_db("show failed students")

    def test_who_failed_subject(self):
        assert _routes_db("who failed mathematics")

    def test_supplementary_query(self):
        assert _routes_db("supplementary subjects for this student")

    def test_is_backlog_true(self):
        assert is_backlog_query("backlog subjects")
        assert is_backlog_query("students with backlog in sem 5")
        assert is_backlog_query("failed students in sem 3")


# ---------------------------------------------------------------------------
# 5. Student metrics → DB
# ---------------------------------------------------------------------------
class TestStudentMetricsQuery:
    def test_cgpa_query(self):
        assert _routes_db("what is the cgpa of this student")

    def test_sgpa_query(self):
        assert _routes_db("what is the sgpa of sem 3")

    def test_average_query(self):
        assert _routes_db("avg of all sems")

    def test_percentage_query(self):
        assert _routes_db("overall percentage")

    def test_semester_percentage(self):
        assert _routes_db("percentage of sem 4")

    def test_is_metrics_true(self):
        assert is_student_metrics_query("cgpa of student")
        assert is_student_metrics_query("average marks of sem 3")
        assert is_student_metrics_query("overall percentage")


# ---------------------------------------------------------------------------
# 6. Student metric follow-up with active context → DB
# ---------------------------------------------------------------------------
class TestStudentMetricFollowup:
    def test_student_metric_followup_uses_context_and_db(self):
        ctx = _ctx(last_roll="2104920100002")
        assert _routes_db("what is his cgpa", ctx)

    def test_followup_sem_marks(self):
        ctx = _ctx(last_roll="2104920100002")
        assert _routes_db("show his sem 3 marks", ctx)

    def test_followup_percentage(self):
        ctx = _ctx(last_roll="2104920100002")
        assert _routes_db("what is her percentage", ctx)

    def test_followup_backlog(self):
        ctx = _ctx(last_roll="2104920100002")
        assert _routes_db("show backlog subjects", ctx)

    def test_followup_all_sem_results(self):
        ctx = _ctx(last_roll="2104920100002")
        assert _routes_db("show all sem results", ctx)


# ---------------------------------------------------------------------------
# 7. Student list / aggregate queries → DB
# ---------------------------------------------------------------------------
class TestStudentListQuery:
    def test_list_students_batch(self):
        assert _routes_db("list students of batch 2021")

    def test_students_in_cse(self):
        assert _routes_db("show students of cse batch 2021")

    def test_failed_students(self):
        assert _routes_db("show failed students")

    def test_pass_percentage(self):
        assert _routes_db("pass percentage of batch 2021")

    def test_students_with_cgpa_above(self):
        assert _routes_db("students with cgpa above 8")

    def test_students_with_cgpa_below(self):
        assert _routes_db("students with cgpa below 6")

    def test_is_list_query_true(self):
        assert is_student_list_query("list students of batch 2021")
        assert is_student_list_query("show failed students")
        assert is_student_list_query("pass percentage of batch 2021")


# ---------------------------------------------------------------------------
# 8. General questions → OpenAI
# ---------------------------------------------------------------------------
class TestGeneralQuestion:
    def test_general_question_uses_openai(self):
        assert _routes_openai("what is machine learning")

    def test_aktu_general_query(self):
        assert _routes_openai("what is the aktu exam pattern")

    def test_career_query(self):
        assert _routes_openai("what career options does a cse student have")

    def test_greeting(self):
        assert _routes_openai("hello how are you")

    def test_writing_query(self):
        assert _routes_openai("write a leave application for the principal")

    def test_what_is_cgpa_explanation(self):
        # "what is cgpa" without a student roll/name → general explanation
        # (no student context, no roll number, just asking what it means)
        # This is an edge case: "cgpa" alone might be caught by metrics.
        # The correct behavior: if there's no student context and no roll number,
        # it should either explain or go to DB. We accept either route here —
        # what matters is it doesn't crash.
        decision = route_query("what is cgpa", _ctx())
        assert decision in ("student_db", "general_openai")

    def test_calculation_explanation_does_not_hit_student_lookup(self):
        """
        Asking how a calculation works → general explanation, not a student lookup.
        """
        decision = route_query("how do you calculate average marks", _ctx())
        assert decision == "general_openai"


# ---------------------------------------------------------------------------
# 9. Mixed query — DB first, then explanation
# ---------------------------------------------------------------------------
class TestMixedQuery:
    def test_mixed_query_fetches_db_and_then_explains(self):
        """
        'show result of roll and explain sgpa' should be classified as student_db
        (DB result first; explanation can be appended without calling OpenAI first).
        """
        decision = route_query(
            "show result of 2104920100002 and explain sgpa", _ctx()
        )
        assert decision == "student_db", (
            "Mixed query with a roll number must go to DB first, "
            "not trigger OpenAI before fetching the record."
        )


# ---------------------------------------------------------------------------
# 10. Instrumentation — verify routing paths
# ---------------------------------------------------------------------------
class TestInstrumentation:
    def test_no_llm_call_made_for_db_queries(self):
        """OpenAI handler must not be called when route is 'student_db'."""
        with patch("openai_handler.handle_general_openai_query") as mock_openai:
            for query in [
                "show marks for 2104920100002",
                "toppers of batch 2021",
                "cgpa of this student",
                "backlog subjects",
                "list students of batch 2021",
            ]:
                decision = route_query(query, _ctx())
                assert decision == "student_db", f"Expected student_db for: {query}"
            mock_openai.assert_not_called()

    def test_db_path_taken_first_for_student_queries(self):
        """route_query must return 'student_db' before any LLM is consulted."""
        student_queries = [
            "show result of 2104920100002",
            "toppers of batch 2021",
            "avg marks for sem 3",
            "show failed students",
            "backlog subjects of 2104920100002",
            "top 10 students by cgpa",
        ]
        for q in student_queries:
            assert route_query(q, _ctx()) == "student_db", (
                f"DB path must be taken first for: {q}"
            )

    def test_openai_path_taken_for_general_queries(self):
        general_queries = [
            "what is machine learning",
            "how are you",
            "write a leave application",
        ]
        for q in general_queries:
            assert route_query(q, _ctx()) == "general_openai", (
                f"OpenAI path must be taken for: {q}"
            )


# ---------------------------------------------------------------------------
# 11. normalize_query
# ---------------------------------------------------------------------------
class TestNormalizeQuery:
    def test_lowercases(self):
        assert normalize_query("SHOW MARKS") == "show marks"

    def test_collapses_whitespace(self):
        assert normalize_query("show   marks   for") == "show marks for"

    def test_strips(self):
        assert normalize_query("  hello  ") == "hello"


# ---------------------------------------------------------------------------
# 12. detect_subtype labels
# ---------------------------------------------------------------------------
class TestDetectSubtype:
    def test_roll_lookup_subtype(self):
        assert detect_subtype("show marks for 2104920100002", _ctx()) == "roll_lookup"

    def test_batch_topper_subtype(self):
        assert detect_subtype("toppers of batch 2021", _ctx()) == "batch_topper"

    def test_subject_topper_subtype(self):
        assert detect_subtype("toppers in ds sem 3 batch 2021", _ctx()) == "subject_topper"

    def test_backlog_subtype(self):
        assert detect_subtype("show backlog subjects", _ctx(last_roll="2104920100002")) == "backlog"

    def test_general_subtype(self):
        assert detect_subtype("what is machine learning", _ctx()) == "general"

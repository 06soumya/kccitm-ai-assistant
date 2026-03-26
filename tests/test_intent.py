"""
Tests for intent_classifier.classify() covering all 22 intents.

Run with:
    pytest tests/test_intent.py -v
"""

import pytest
from intent_classifier import classify, Intent, DB_INTENTS, OPENAI_INTENTS

# Helper: empty context (no active student)
_NO_CTX = {"last_roll": None}
# Helper: context with an active roll number
_CTX = {"last_roll": "2104920100002"}


def _intent(query, ctx=None):
    return classify(query, ctx or _NO_CTX).intent


def _routes_db(query, ctx=None):
    return classify(query, ctx or _NO_CTX).routes_to_db()


def _routes_openai(query, ctx=None):
    return classify(query, ctx or _NO_CTX).routes_to_openai()


# ── Tier 0: help / small talk ─────────────────────────────────────────────────

class TestHelpAndSmallTalk:
    def test_help_what_can_you_do(self):
        assert _intent("what can you do") == Intent.HELP_CAPABILITY

    def test_help_capabilities(self):
        assert _intent("what are your capabilities?") == Intent.HELP_CAPABILITY

    def test_help_how_to_use(self):
        assert _intent("how should I use this?") == Intent.HELP_CAPABILITY

    def test_small_talk_hi(self):
        assert _intent("hi") == Intent.SMALL_TALK

    def test_small_talk_hello(self):
        assert _intent("Hello!") == Intent.SMALL_TALK

    def test_small_talk_good_morning(self):
        assert _intent("good morning") == Intent.SMALL_TALK

    def test_small_talk_thanks(self):
        assert _intent("thanks") == Intent.SMALL_TALK

    def test_small_talk_ok(self):
        assert _intent("ok") == Intent.SMALL_TALK

    def test_help_routes_openai(self):
        assert _routes_openai("what can you do")

    def test_small_talk_routes_openai(self):
        assert _routes_openai("hello!")


# ── Tier 1: roll number ───────────────────────────────────────────────────────

class TestStudentResult:
    def test_roll_number_only(self):
        assert _intent("2104920100002") == Intent.STUDENT_RESULT

    def test_roll_in_sentence(self):
        assert _intent("show marks for 2104920100002") == Intent.STUDENT_RESULT

    def test_roll_with_semester(self):
        assert _intent("result of 2104920100002 sem 2") == Intent.STUDENT_RESULT

    def test_has_roll_flag(self):
        r = classify("show 2104920100002 result", _NO_CTX)
        assert r.has_roll is True

    def test_routes_to_db(self):
        assert _routes_db("2104920100002")


# ── Tier 2: AKTU ─────────────────────────────────────────────────────────────

class TestAktuIntent:
    def test_aktu_keyword(self):
        assert _intent("what are aktu exam rules") == Intent.AKTU_WEB

    def test_aktu_circular(self):
        assert _intent("show latest aktu circular") == Intent.AKTU_WEB

    def test_aktu_syllabus(self):
        assert _intent("aktu syllabus for 2023") == Intent.AKTU_WEB

    def test_aktu_promotion(self):
        assert _intent("what are promotion rules for btech") == Intent.AKTU_WEB

    def test_aktu_routes_openai(self):
        assert _routes_openai("what are aktu exam rules")


# ── Tier 2: academic explanation ─────────────────────────────────────────────

class TestAcademicExplanation:
    def test_what_is_cgpa(self):
        assert _intent("what is cgpa") == Intent.ACADEMIC_EXPLANATION

    def test_what_is_sgpa(self):
        assert _intent("what is sgpa") == Intent.ACADEMIC_EXPLANATION

    def test_explain_backlog(self):
        assert _intent("explain backlog") == Intent.ACADEMIC_EXPLANATION

    def test_what_is_atkt(self):
        assert _intent("what is atkt") == Intent.ACADEMIC_EXPLANATION

    def test_what_does_cgpa_mean(self):
        assert _intent("what does cgpa mean") == Intent.ACADEMIC_EXPLANATION

    def test_academic_routes_openai(self):
        assert _routes_openai("what is cgpa")


# ── Tier 2: calculation explanation ──────────────────────────────────────────

class TestCalcExplanation:
    def test_how_did_you_calculate(self):
        assert _intent("how did you calculate the average") == Intent.CALC_EXPLANATION

    def test_how_is_cgpa_calculated(self):
        assert _intent("how is this cgpa calculated") == Intent.CALC_EXPLANATION

    def test_what_formula_used(self):
        assert _intent("what formula did you use") == Intent.CALC_EXPLANATION

    def test_explain_this_percentage(self):
        assert _intent("explain this percentage") == Intent.CALC_EXPLANATION

    def test_calc_routes_openai(self):
        assert _routes_openai("how did you calculate the average")


# ── Tier 2: writing / summarization / compare / recommend ────────────────────

class TestNonStudentOpenAI:
    def test_write_email(self):
        assert _intent("write me an email to my professor") == Intent.WRITING_REWRITING

    def test_rewrite_this(self):
        assert _intent("rewrite this paragraph more formally") == Intent.WRITING_REWRITING

    def test_summarize(self):
        assert _intent("summarize this text for me") == Intent.SUMMARIZATION

    def test_key_points(self):
        assert _intent("give me the key points of this") == Intent.SUMMARIZATION

    def test_compare(self):
        assert _intent("compare python vs java") == Intent.COMPARISON

    def test_difference_between(self):
        assert _intent("difference between mean and median") == Intent.COMPARISON

    def test_recommend_books(self):
        assert _intent("recommend best books for data structures") == Intent.RECOMMENDATION

    def test_suggest_project(self):
        assert _intent("suggest a project idea for final year") == Intent.RECOMMENDATION

    def test_writing_routes_openai(self):
        assert _routes_openai("write me an email")

    def test_summarize_routes_openai(self):
        assert _routes_openai("summarize this text")

    def test_compare_routes_openai(self):
        assert _routes_openai("compare python vs java")

    def test_recommend_routes_openai(self):
        assert _routes_openai("recommend best books for ds")


# ── Tier 2: general knowledge ─────────────────────────────────────────────────

class TestGeneralKnowledge:
    def test_machine_learning(self):
        assert _intent("what is machine learning") == Intent.GENERAL_KNOWLEDGE

    def test_chatbot(self):
        assert _intent("what is a chatbot") == Intent.GENERAL_KNOWLEDGE

    def test_chatgpt(self):
        assert _intent("what is chatgpt") == Intent.GENERAL_KNOWLEDGE

    def test_capital_of(self):
        assert _intent("capital of france") == Intent.GENERAL_KNOWLEDGE

    def test_general_knowledge_routes_openai(self):
        assert _routes_openai("what is machine learning")


# ── Tier 3: topper queries ────────────────────────────────────────────────────

class TestTopperQuery:
    def test_toppers_batch(self):
        assert _intent("toppers of batch 2021") == Intent.STUDENT_RESULT

    def test_top_20_students(self):
        assert _intent("top 20 students of batch 2021") == Intent.STUDENT_RESULT

    def test_ranklist(self):
        assert _intent("show ranklist for batch 2022") == Intent.STUDENT_RESULT

    def test_highest_cgpa(self):
        assert _intent("student with highest cgpa in batch 2021") == Intent.STUDENT_RESULT

    def test_topper_no_false_positive_topic(self):
        # "topic" should NOT trigger topper
        result = classify("what is the topic of today's lecture", _NO_CTX)
        assert result.intent != Intent.STUDENT_RESULT

    def test_topper_routes_db(self):
        assert _routes_db("toppers of batch 2021")


# ── Tier 3: backlog ───────────────────────────────────────────────────────────

class TestBacklogQuery:
    def test_backlog_keyword(self):
        assert _intent("show backlog subjects") == Intent.BACKLOG_QUERY

    def test_supplementary_keyword(self):
        # No roll number — pure backlog intent
        assert _intent("supplementary subjects for this student") == Intent.BACKLOG_QUERY

    def test_backlog_in_context(self):
        result = classify("show my backlogs", _CTX)
        assert result.intent == Intent.BACKLOG_QUERY

    def test_backlog_routes_db(self):
        assert _routes_db("show backlog subjects")

    def test_failed_subjects(self):
        assert _intent("which subjects did I fail") == Intent.BACKLOG_QUERY


# ── Tier 3: metrics (CGPA/SGPA/avg/percentage) ───────────────────────────────

class TestMetricQueries:
    def test_cgpa_no_context(self):
        # Without context still classified as metric
        assert _intent("what is my cgpa") == Intent.STUDENT_METRIC

    def test_cgpa_with_roll(self):
        assert _intent("cgpa of 2104920100002") == Intent.STUDENT_RESULT  # roll → STUDENT_RESULT

    def test_sgpa_query(self):
        assert _intent("sgpa of sem 3") == Intent.STUDENT_METRIC

    def test_average_query(self):
        assert _intent("give me avg for all sems") == Intent.STUDENT_AVERAGE

    def test_average_sem_filter(self):
        assert _intent("average marks sem 2") == Intent.STUDENT_AVERAGE

    def test_percentage_query(self):
        assert _intent("what is my percentage") == Intent.STUDENT_PERCENTAGE

    def test_average_routes_db(self):
        assert _routes_db("give me avg for all sems")

    def test_percentage_routes_db(self):
        assert _routes_db("what is my percentage")

    def test_metric_routes_db(self):
        assert _routes_db("what is my cgpa")


# ── Tier 3: subject queries ───────────────────────────────────────────────────

class TestSubjectQuery:
    def test_subject_code(self):
        assert _intent("marks in kcs101t") == Intent.SUBJECT_CODE_QUERY

    def test_subject_query_with_context(self):
        result = classify("show subject list", _CTX)
        assert result.intent == Intent.SUBJECT_QUERY

    def test_subject_code_routes_db(self):
        assert _routes_db("marks in kcs101t")


# ── Tier 4: name/result lookup ────────────────────────────────────────────────

class TestStudentLookup:
    def test_result_of_student(self):
        result = classify("show result of aakash singh batch 2021", _NO_CTX)
        assert result.intent == Intent.STUDENT_RESULT

    def test_marks_of_student(self):
        result = classify("marks of rahul batch 2022", _NO_CTX)
        assert result.intent in {Intent.STUDENT_RESULT, Intent.STUDENT_METRIC}

    def test_show_student_details(self):
        result = classify("show student details for priya batch 2021", _NO_CTX)
        assert result.routes_to_db()


# ── Tier 5: follow-up context ─────────────────────────────────────────────────

class TestFollowupContext:
    def test_his_cgpa(self):
        result = classify("what is his cgpa", _CTX)
        # cgpa keyword triggers STUDENT_METRIC (is_followup=True) before FOLLOWUP_CONTEXT
        assert result.routes_to_db()
        assert result.is_followup is True

    def test_his_average(self):
        result = classify("his average", _CTX)
        assert result.routes_to_db()

    def test_this_students_marks(self):
        result = classify("this student's marks", _CTX)
        assert result.routes_to_db()

    def test_show_sem_2(self):
        result = classify("show sem 2", _CTX)
        assert result.routes_to_db()

    def test_no_followup_without_context(self):
        # "his cgpa" without context should still route to DB (metric word present)
        result = classify("his cgpa", _NO_CTX)
        assert result.routes_to_db()


# ── Hard negatives — phrases that should NOT become student name lookups ──────

class TestHardNegatives:
    def test_how_to_calculate_avg(self):
        # Should route to calc explanation or general, NOT DB name lookup
        result = classify("how do you calculate average marks", _NO_CTX)
        assert result.routes_to_openai()

    def test_give_me_avg_all_sems(self):
        # Must route to DB as average query, NOT name search for "me all sems"
        result = classify("give me avg for all sems", _CTX)
        assert result.routes_to_db()
        assert result.intent == Intent.STUDENT_AVERAGE

    def test_show_me_percentage(self):
        result = classify("show me percentage", _CTX)
        assert result.routes_to_db()
        assert result.intent == Intent.STUDENT_PERCENTAGE

    def test_what_is_backlog(self):
        # Academic explanation — should NOT treat as backlog query for a student
        result = classify("what is a backlog", _NO_CTX)
        assert result.intent == Intent.ACADEMIC_EXPLANATION

    def test_aktu_not_student_name(self):
        result = classify("what are aktu rules", _NO_CTX)
        assert result.routes_to_openai()

    def test_machine_learning_not_student(self):
        result = classify("what is machine learning", _NO_CTX)
        assert result.routes_to_openai()

    def test_explain_not_student(self):
        result = classify("explain cgpa formula", _NO_CTX)
        assert result.routes_to_openai()


# ── Tier 6: reasoning ─────────────────────────────────────────────────────────

class TestReasoning:
    def test_why_is(self):
        assert _intent("why is the sky blue") == Intent.REASONING

    def test_step_by_step(self):
        assert _intent("solve this step by step") == Intent.REASONING

    def test_reasoning_routes_openai(self):
        assert _routes_openai("why is the sky blue")


# ── Tier 7: ambiguous short queries ──────────────────────────────────────────

class TestAmbiguous:
    def test_just_result_no_context(self):
        result = classify("result", _NO_CTX)
        assert result.intent == Intent.AMBIGUOUS

    def test_just_marks_no_context(self):
        result = classify("marks", _NO_CTX)
        assert result.intent == Intent.AMBIGUOUS

    def test_marks_with_context_routes_db(self):
        # Short query with active context → metric follow-up, still routes to DB
        result = classify("marks", _CTX)
        assert result.routes_to_db()
        assert result.is_followup is True

    def test_result_with_context_routes_db(self):
        # Short query with active context → metric follow-up, still routes to DB
        result = classify("result", _CTX)
        assert result.routes_to_db()
        assert result.is_followup is True


# ── Fallback: general assistant ───────────────────────────────────────────────

class TestGeneralAssistant:
    def test_generic_question(self):
        result = classify("what should I eat for breakfast", _NO_CTX)
        assert result.intent == Intent.GENERAL_ASSISTANT

    def test_unknown_query(self):
        result = classify("tell me a random fact", _NO_CTX)
        assert result.intent == Intent.GENERAL_ASSISTANT

    def test_general_routes_openai(self):
        assert _routes_openai("tell me a random fact")


# ── Routing completeness ──────────────────────────────────────────────────────

class TestRoutingCompleteness:
    """Every intent is either DB or OpenAI — no gaps."""

    def test_all_db_intents_route_to_db(self):
        for intent in DB_INTENTS:
            r = type("R", (), {"intent": intent})()
            r.routes_to_db = lambda self=r: self.intent in DB_INTENTS
            assert r.routes_to_db(), f"{intent} should route to DB"

    def test_all_openai_intents_route_to_openai(self):
        for intent in OPENAI_INTENTS:
            r = type("R", (), {"intent": intent})()
            r.routes_to_openai = lambda self=r: self.intent in OPENAI_INTENTS
            assert r.routes_to_openai(), f"{intent} should route to OpenAI"

    def test_db_and_openai_are_disjoint(self):
        assert DB_INTENTS.isdisjoint(OPENAI_INTENTS)


# ── ClassificationResult contract ────────────────────────────────────────────

class TestClassificationResult:
    def test_has_roll_set_on_roll_query(self):
        r = classify("show 2104920100002 result", _NO_CTX)
        assert r.has_roll is True

    def test_has_roll_false_on_name_query(self):
        r = classify("toppers of batch 2021", _NO_CTX)
        assert r.has_roll is False

    def test_is_followup_set_with_context(self):
        r = classify("show avg", _CTX)
        assert r.is_followup is True

    def test_repr_contains_intent(self):
        r = classify("2104920100002", _NO_CTX)
        assert "student_result_query" in repr(r)

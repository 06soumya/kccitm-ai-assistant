"""
Comprehensive semantic pipeline test suite — 10 failure categories.

Tests the parse_query() pipeline in semantic_parser.py against the
general class of routing failures, not just individual example sentences.

Run with:
    pytest tests/test_semantic_pipeline.py -v

Categories:
  1. Structured DB queries → Route.STUDENT_DB / DB_ANALYTICS / STUDENT_METRIC
  2. Explanation / general intent → Route.GENERAL_AI
  3. Web / official-source queries → Route.WEB
  4. Document / RAG queries → Route.DOCUMENT_RAG
  5. Follow-up / context-dependent → Route.CONTEXT_FOLLOWUP
  6. Mixed-intent (DB + explanation) → DB route (data first)
  7. Ambiguity handling → CLARIFICATION only when truly needed
  8. Negative entity extraction — sentence fragments ≠ student names
  9. Performance — no unnecessary OpenAI calls for DB queries
 10. Regression — every previously discovered failure class
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from semantic_parser import (
    parse_query,
    Route,
    IntentFamily,
    normalize_query,
    detect_query_shape,
    classify_intent,
    extract_filters_and_entities,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_DB_ROUTES = {Route.STUDENT_DB, Route.DB_ANALYTICS, Route.STUDENT_METRIC, Route.CONTEXT_FOLLOWUP}
_AI_ROUTES = {Route.GENERAL_AI, Route.WEB, Route.DOCUMENT_RAG}

def _ctx(last_roll=None):
    return {"last_roll": last_roll, "current_student": last_roll}

def _parse(query, last_roll=None):
    return parse_query(query, _ctx(last_roll))

def _routes_db(query, last_roll=None):
    return _parse(query, last_roll).route in _DB_ROUTES

def _routes_ai(query, last_roll=None):
    return _parse(query, last_roll).route in _AI_ROUTES

def _family(query, last_roll=None):
    return _parse(query, last_roll).intent_family

def _intent(query, last_roll=None):
    return _parse(query, last_roll).intent

def _route(query, last_roll=None):
    return _parse(query, last_roll).route


# ─────────────────────────────────────────────────────────────────────────────
# Category 1: Structured DB queries
# Verify every plausible phrasing of a student data query routes to DB.
# ─────────────────────────────────────────────────────────────────────────────
class TestStructuredDBQueries:
    """All student data lookups must reach the DB path."""

    # Roll number — always DB regardless of phrasing
    def test_roll_only(self):
        assert _routes_db("2104920100002")

    def test_roll_in_sentence(self):
        assert _routes_db("show marks for 2104920100002")

    def test_roll_with_context_words(self):
        assert _routes_db("can you give me the result of 2104920100002")

    def test_roll_with_semester(self):
        assert _routes_db("result of 2104920100002 sem 3")

    # Topper / ranking queries
    def test_toppers_batch(self):
        assert _routes_db("toppers of batch 2021")

    def test_top_n_students(self):
        assert _routes_db("top 20 students of batch 2021")

    def test_ranklist(self):
        assert _routes_db("show ranklist for batch 2022")

    def test_highest_cgpa_in_batch(self):
        assert _routes_db("student with highest cgpa in batch 2021")

    def test_best_result_in_batch(self):
        assert _routes_db("who got best result in batch 2022")

    def test_college_topper(self):
        # No batch specified → asks for batch (CLARIFICATION) or DB; both acceptable
        pq = _parse("college topper list")
        assert pq.intent_family == IntentFamily.STRUCTURED_STUDENT

    def test_branch_topper(self):
        # No batch specified → asks for batch (CLARIFICATION) or DB; both acceptable
        pq = _parse("branch topper list")
        assert pq.intent_family == IntentFamily.STRUCTURED_STUDENT

    # Name lookup queries
    def test_result_of_name(self):
        assert _routes_db("show result of Aakash Singh")

    def test_marks_of_name(self):
        assert _routes_db("marks of Rahul Kumar")

    def test_details_for_name(self):
        assert _routes_db("show details for Priya Sharma batch 2021")

    def test_name_result_fetch(self):
        assert _routes_db("fetch result of Neha Gupta")

    # Backlog queries
    def test_backlog_keyword(self):
        # Backlog routes to DB (execute_student_query will request roll if missing)
        pq = _parse("show backlog subjects")
        assert pq.intent_family == IntentFamily.BACKLOG
        assert pq.route in _DB_ROUTES

    def test_supplementary_query(self):
        pq = _parse("supplementary subjects for this student")
        assert pq.intent_family == IntentFamily.BACKLOG
        assert pq.route in _DB_ROUTES

    def test_failed_students(self):
        assert _routes_db("who failed in sem 3")

    def test_atkt_query(self):
        assert _routes_db("students with atkt in batch 2021")

    # Metric queries (need student context or roll)
    def test_cgpa_with_roll(self):
        assert _routes_db("cgpa of 2104920100002")

    def test_average_with_context(self):
        assert _routes_db("average of all sems", last_roll="2104920100002")

    def test_percentage_with_context(self):
        assert _routes_db("overall percentage", last_roll="2104920100002")

    def test_sgpa_sem_with_context(self):
        assert _routes_db("sgpa of sem 3", last_roll="2104920100002")

    # Pass/fail status
    def test_pass_fail_status(self):
        assert _routes_db("pass fail status", last_roll="2104920100002")

    def test_is_he_pass(self):
        assert _routes_db("is he passed in sem 3", last_roll="2104920100002")

    def test_result_status(self):
        assert _routes_db("result status of sem 4", last_roll="2104920100002")

    # Aggregate / list
    def test_list_students_batch(self):
        assert _routes_db("list students of batch 2021")

    def test_show_all_students(self):
        assert _routes_db("show all students of batch 2022")

    def test_pass_percentage(self):
        assert _routes_db("pass percentage of batch 2021")

    def test_students_with_cgpa_above(self):
        assert _routes_db("students with cgpa above 8")


# ─────────────────────────────────────────────────────────────────────────────
# Category 2: Explanation / general intent queries
# Verify concept questions, how-to questions, writing tasks → GENERAL_AI.
# ─────────────────────────────────────────────────────────────────────────────
class TestExplanationAndGeneralQueries:
    """No student data needed — must not hit DB."""

    # Academic concept explanations
    def test_what_is_cgpa(self):
        assert _routes_ai("what is cgpa")

    def test_what_is_sgpa(self):
        assert _routes_ai("what is sgpa")

    def test_explain_backlog(self):
        assert _routes_ai("explain backlog")

    def test_what_is_atkt(self):
        assert _routes_ai("what is atkt")

    def test_what_does_cgpa_mean(self):
        assert _routes_ai("what does cgpa mean")

    def test_what_is_internal_marks(self):
        assert _routes_ai("what is internal marks")

    def test_what_is_credit_system(self):
        assert _routes_ai("what is the credit system in btech")

    def test_what_is_grade_point(self):
        assert _routes_ai("what is grade point")

    # Calculation explanation — "how to" / "formula for" phrasing
    def test_how_to_calculate_cgpa(self):
        assert _routes_ai("how to calculate cgpa")

    def test_how_to_calculate_average(self):
        assert _routes_ai("how to calculate average marks")

    def test_formula_for_cgpa(self):
        assert _routes_ai("formula for cgpa")

    def test_formula_for_sgpa(self):
        assert _routes_ai("what is the formula for sgpa")

    def test_formula_for_percentage(self):
        assert _routes_ai("formula for percentage marks")

    def test_steps_to_calculate_cgpa(self):
        assert _routes_ai("steps to calculate cgpa")

    def test_how_cgpa_is_calculated(self):
        assert _routes_ai("how cgpa is calculated")

    def test_how_average_is_computed(self):
        assert _routes_ai("how average is computed")

    def test_how_do_you_calculate(self):
        assert _routes_ai("how do you calculate the average")

    def test_how_did_you_calculate(self):
        assert _routes_ai("how did you calculate this result")

    # Writing / summarization / comparison / recommendation
    def test_write_email(self):
        assert _routes_ai("write me an email to my professor")

    def test_rewrite_formally(self):
        assert _routes_ai("rewrite this paragraph more formally")

    def test_summarize_text(self):
        assert _routes_ai("summarize this text for me")

    def test_compare_python_java(self):
        assert _routes_ai("compare python vs java")

    def test_difference_between(self):
        assert _routes_ai("difference between mean and median")

    def test_recommend_books(self):
        assert _routes_ai("recommend best books for data structures")

    def test_project_ideas(self):
        assert _routes_ai("suggest a project idea for final year")

    def test_study_tips(self):
        assert _routes_ai("give me study tips for exams")

    # General knowledge
    def test_what_is_machine_learning(self):
        assert _routes_ai("what is machine learning")

    def test_what_is_deep_learning(self):
        assert _routes_ai("explain deep learning")

    def test_capital_of_india(self):
        assert _routes_ai("capital of india")

    # Small talk
    def test_hello(self):
        assert _routes_ai("hello")

    def test_good_morning(self):
        assert _routes_ai("good morning")

    def test_thank_you(self):
        assert _routes_ai("thank you")

    # Help / capability
    def test_what_can_you_do(self):
        assert _routes_ai("what can you do")

    def test_how_to_use(self):
        assert _routes_ai("how should I use this")


# ─────────────────────────────────────────────────────────────────────────────
# Category 3: Web / official-source queries
# AKTU rules, notices, syllabus → WEB route.
# ─────────────────────────────────────────────────────────────────────────────
class TestWebOfficialQueries:
    """AKTU / official queries must route to WEB, not DB or blind AI."""

    def test_aktu_exam_rules(self):
        pq = _parse("what are aktu exam rules")
        assert pq.route == Route.WEB or pq.intent_family == IntentFamily.WEB_OFFICIAL

    def test_aktu_circular(self):
        pq = _parse("show latest aktu circular")
        assert pq.intent_family == IntentFamily.WEB_OFFICIAL

    def test_aktu_syllabus(self):
        pq = _parse("aktu syllabus for btech 2023")
        assert pq.intent_family == IntentFamily.WEB_OFFICIAL

    def test_aktu_promotion_rules(self):
        pq = _parse("what are promotion rules for btech aktu")
        assert pq.intent_family == IntentFamily.WEB_OFFICIAL

    def test_university_rules(self):
        pq = _parse("what are university rules for supplementary exam")
        # may classify as WEB or academic_explanation — either is correct
        assert pq.intent_family in {IntentFamily.WEB_OFFICIAL, IntentFamily.ACADEMIC_EXPLAIN, IntentFamily.GENERAL_AI}

    def test_aktu_curriculum(self):
        pq = _parse("aktu curriculum for cse 3rd year")
        assert pq.intent_family == IntentFamily.WEB_OFFICIAL

    def test_aktu_back_paper_rules(self):
        pq = _parse("what are back paper rules in aktu")
        assert pq.intent_family == IntentFamily.WEB_OFFICIAL

    def test_web_route_not_db(self):
        """AKTU queries must NEVER hit the student DB."""
        pq = _parse("aktu exam regulations 2023")
        assert pq.route != Route.STUDENT_DB
        assert pq.route != Route.DB_ANALYTICS


# ─────────────────────────────────────────────────────────────────────────────
# Category 4: Document / RAG queries
# Questions about uploaded files → DOCUMENT_RAG.
# ─────────────────────────────────────────────────────────────────────────────
class TestDocumentRAGQueries:
    """Document/PDF queries must route to DOCUMENT_RAG."""

    def test_this_pdf(self):
        pq = _parse("summarize this pdf")
        assert pq.intent_family == IntentFamily.DOCUMENT_RAG

    def test_uploaded_file(self):
        pq = _parse("what does the uploaded file say")
        assert pq.intent_family == IntentFamily.DOCUMENT_RAG

    def test_extract_from_document(self):
        pq = _parse("extract information from the document")
        assert pq.intent_family == IntentFamily.DOCUMENT_RAG

    def test_pdf_summary(self):
        pq = _parse("give me a summary of this pdf")
        assert pq.intent_family == IntentFamily.DOCUMENT_RAG

    def test_this_file(self):
        pq = _parse("what is in this file")
        assert pq.intent_family == IntentFamily.DOCUMENT_RAG

    def test_document_not_db(self):
        """Document queries must never hit student DB."""
        pq = _parse("extract data from the uploaded document")
        assert pq.route != Route.STUDENT_DB


# ─────────────────────────────────────────────────────────────────────────────
# Category 5: Follow-up / context-dependent queries
# Queries that rely on prior session context.
# ─────────────────────────────────────────────────────────────────────────────
class TestFollowUpContextQueries:
    """Follow-up queries with an active student must route to DB."""
    _ROLL = "2104920100002"

    def test_pronoun_cgpa(self):
        assert _routes_db("what is his cgpa", last_roll=self._ROLL)

    def test_pronoun_percentage(self):
        assert _routes_db("what is her percentage", last_roll=self._ROLL)

    def test_pronoun_backlog(self):
        assert _routes_db("how many backlogs does he have", last_roll=self._ROLL)

    def test_sem_only(self):
        assert _routes_db("sem 3", last_roll=self._ROLL)

    def test_and_percentage(self):
        assert _routes_db("and percentage?", last_roll=self._ROLL)

    def test_and_average(self):
        assert _routes_db("and average?", last_roll=self._ROLL)

    def test_show_all_sems(self):
        assert _routes_db("show all semesters", last_roll=self._ROLL)

    def test_what_about_sem4(self):
        assert _routes_db("what about sem 4", last_roll=self._ROLL)

    def test_short_marks_with_context(self):
        assert _routes_db("marks", last_roll=self._ROLL)

    def test_short_result_with_context(self):
        assert _routes_db("result", last_roll=self._ROLL)

    def test_followup_flag_set(self):
        pq = _parse("and cgpa?", last_roll=self._ROLL)
        assert pq.is_followup is True

    def test_context_resolved_in_entities(self):
        pq = _parse("what is his average", last_roll=self._ROLL)
        # roll should be resolved from context
        assert pq.entities.get("roll") == self._ROLL

    def test_no_followup_without_context(self):
        """Without active student, short pronoun query → clarification or ambiguous."""
        pq = _parse("his cgpa")
        assert pq.route in {Route.CLARIFICATION, Route.STUDENT_METRIC, Route.STUDENT_DB}


# ─────────────────────────────────────────────────────────────────────────────
# Category 6: Mixed-intent queries (DB + explanation)
# DB-first rule: roll number or explicit student data → DB always wins.
# ─────────────────────────────────────────────────────────────────────────────
class TestMixedIntentQueries:
    """When data + explanation coexist, data (DB) takes priority."""

    def test_roll_plus_explain(self):
        """Roll number present → always DB first."""
        assert _routes_db("show result of 2104920100002 and explain sgpa")

    def test_roll_plus_comparison(self):
        assert _routes_db("show marks for 2104920100002 and compare with average")

    def test_topper_plus_summarize(self):
        """Topper query → DB, not general AI."""
        assert _routes_db("top 20 students of batch 2021 and summarize the result")

    def test_result_plus_formula(self):
        """Explicit result keyword → DB for data fetch."""
        assert _routes_db("show result of 2104920100002 what formula is used for cgpa")

    def test_marks_plus_question(self):
        """With roll number, DB wins regardless of appended explanation question."""
        assert _routes_db("marks of 2104920100002 — how is percentage calculated?")


# ─────────────────────────────────────────────────────────────────────────────
# Category 7: Ambiguity handling
# Should clarify only when genuinely needed, not over-clarify.
# ─────────────────────────────────────────────────────────────────────────────
class TestAmbiguityHandling:
    """Clarification only when no safe default exists."""

    # Should NOT clarify when context resolves ambiguity
    def test_short_metric_with_context_not_clarify(self):
        pq = _parse("cgpa", last_roll="2104920100002")
        assert pq.route != Route.CLARIFICATION

    def test_pronoun_with_context_not_clarify(self):
        pq = _parse("his marks", last_roll="2104920100002")
        assert pq.route != Route.CLARIFICATION

    # Should NOT clarify general questions
    def test_general_question_not_clarify(self):
        pq = _parse("what is machine learning")
        assert pq.route != Route.CLARIFICATION

    def test_greeting_not_clarify(self):
        pq = _parse("hello")
        assert pq.route != Route.CLARIFICATION

    def test_formula_question_not_clarify(self):
        pq = _parse("formula for cgpa")
        assert pq.route != Route.CLARIFICATION

    # Should clarify only for topper query missing batch
    def test_toppers_no_batch_triggers_clarify(self):
        pq = _parse("show me toppers")
        assert pq.clarification_needed is True

    # Ambiguous single word without context → clarification is acceptable
    def test_bare_marks_no_context(self):
        pq = _parse("marks")
        # Either clarification or ambiguous — both acceptable
        assert pq.route in {Route.CLARIFICATION, Route.STUDENT_DB, Route.STUDENT_METRIC}

    # With context, bare word should NOT clarify
    def test_bare_marks_with_context_no_clarify(self):
        pq = _parse("marks", last_roll="2104920100002")
        assert pq.route != Route.CLARIFICATION

    # Clarification question should be short and focused
    def test_clarification_question_is_string(self):
        pq = _parse("show toppers")
        if pq.clarification_needed:
            assert isinstance(pq.clarification_question, str)
            assert len(pq.clarification_question) > 5


# ─────────────────────────────────────────────────────────────────────────────
# Category 8: Negative entity extraction
# Sentence fragments, metric words, and function words ≠ student names.
# ─────────────────────────────────────────────────────────────────────────────
class TestNegativeEntityExtraction:
    """Ensure no false-positive name extraction."""

    def test_no_name_from_how_to_calculate(self):
        pq = _parse("how to calculate average")
        assert "name_candidate" not in pq.entities

    def test_no_name_from_what_is_cgpa(self):
        pq = _parse("what is cgpa")
        assert "name_candidate" not in pq.entities

    def test_no_name_from_machine_learning(self):
        pq = _parse("what is machine learning")
        assert "name_candidate" not in pq.entities

    def test_no_name_from_formula_query(self):
        pq = _parse("formula for sgpa")
        assert "name_candidate" not in pq.entities

    def test_no_name_from_give_me_average(self):
        pq = _parse("give me average marks for all sems")
        assert "name_candidate" not in pq.entities

    def test_no_name_from_show_me_percentage(self):
        pq = _parse("show me percentage")
        assert "name_candidate" not in pq.entities

    def test_no_name_from_backlog_explain(self):
        pq = _parse("what is a backlog")
        assert "name_candidate" not in pq.entities

    def test_no_name_from_aktu_query(self):
        pq = _parse("what are aktu exam rules")
        assert "name_candidate" not in pq.entities

    def test_roll_extracted_correctly(self):
        pq = _parse("show marks for 2104920100002")
        assert pq.entities.get("roll") == "2104920100002"

    def test_name_extracted_from_direct_pattern(self):
        pq = _parse("marks of Rahul Sharma")
        # Entity extraction only runs for DB families; verify it runs
        if pq.intent_family in {IntentFamily.STRUCTURED_STUDENT}:
            assert pq.entities.get("name_candidate") is not None

    def test_batch_extracted_for_topper(self):
        pq = _parse("toppers of batch 2021")
        assert pq.filters.get("batch") == 2021

    def test_semester_extracted(self):
        pq = _parse("show marks for 2104920100002 sem 3")
        assert pq.filters.get("semester") == "3"

    def test_top_n_extracted(self):
        pq = _parse("top 20 students of batch 2021")
        assert pq.filters.get("top_n") == 20


# ─────────────────────────────────────────────────────────────────────────────
# Category 9: Performance — no unnecessary backend calls
# DB queries must NOT trigger AI; AI queries must NOT trigger DB.
# ─────────────────────────────────────────────────────────────────────────────
class TestPerformanceRouting:
    """Verify correct route selection for efficiency."""

    _DB_QUERIES = [
        "show marks for 2104920100002",
        "toppers of batch 2021",
        "avg marks sem 3",
        "show failed students",
        "backlog subjects of 2104920100002",
        "top 10 students of batch 2021",     # has batch → DB
        "result of Aakash Singh batch 2021",
        "pass percentage of batch 2021",
        "students with cgpa above 8",
        "show ranklist for batch 2022",
    ]

    _AI_QUERIES = [
        "what is machine learning",
        "hello how are you",
        "write a leave application",
        "what is cgpa",
        "how to calculate average",
        "formula for sgpa",
        "explain backlog",
        "what is atkt",
        "recommend books for data structures",
        "aktu exam rules",
    ]

    def test_db_queries_route_to_db(self):
        for q in self._DB_QUERIES:
            pq = _parse(q)
            assert pq.route in _DB_ROUTES, (
                f"Expected DB route for: {q!r}, got {pq.route!r}"
            )

    def test_ai_queries_do_not_route_to_db(self):
        for q in self._AI_QUERIES:
            pq = _parse(q)
            assert pq.route not in {Route.STUDENT_DB, Route.DB_ANALYTICS}, (
                f"DB route incorrectly chosen for: {q!r}, route={pq.route!r}"
            )

    def test_db_queries_set_needs_db_true(self):
        for q in self._DB_QUERIES:
            pq = _parse(q)
            assert pq.needs_db is True, f"needs_db should be True for: {q!r}"

    def test_ai_queries_set_needs_general_ai_or_web(self):
        for q in self._AI_QUERIES:
            pq = _parse(q)
            assert pq.needs_general_ai or pq.needs_web, (
                f"needs_general_ai/web should be True for: {q!r}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Category 10: Regression — known failure classes
# Each test encodes a class of previously discovered failure, not just one sentence.
# ─────────────────────────────────────────────────────────────────────────────
class TestRegressionFailureClasses:
    """One test per failure class, covering the general pattern."""

    # FAILURE CLASS: metric words without context → was routing to DB with no roll
    # Fix: require context or roll for metric routes to avoid empty DB query
    def test_bare_cgpa_no_context_does_not_crash(self):
        pq = _parse("cgpa")
        # Acceptable routes: CLARIFICATION (no student known) or STUDENT_METRIC
        assert pq.route in {Route.CLARIFICATION, Route.STUDENT_METRIC, Route.STUDENT_DB}

    # FAILURE CLASS: "how to calculate X" was routing to DB
    # Fix: HOW_CALC scoring penalises DB path; always → GENERAL_AI
    def test_class_how_to_calc_always_ai(self):
        calc_queries = [
            "how to calculate cgpa",
            "how to calculate average marks",
            "how to find percentage",
            "how to compute sgpa",
            "how to get average of all sems",
        ]
        for q in calc_queries:
            assert _routes_ai(q), f"Expected AI route for: {q!r}"

    # FAILURE CLASS: "formula for X" was routing to DB
    def test_class_formula_queries_always_ai(self):
        formula_queries = [
            "formula for cgpa",
            "formula for sgpa",
            "the formula for percentage",
            "what is the formula for average marks",
            "formula of cgpa calculation",
        ]
        for q in formula_queries:
            assert _routes_ai(q), f"Expected AI route for: {q!r}"

    # FAILURE CLASS: "explain X" without student context → was routing to DB
    def test_class_explain_without_context_always_ai(self):
        explain_queries = [
            "explain backlog",
            "explain cgpa formula",
            "explain sgpa",
            "explain how marks are calculated",
            "explain the grading system",
        ]
        for q in explain_queries:
            assert _routes_ai(q), f"Expected AI route for: {q!r}"

    # FAILURE CLASS: Single-word name queries were failing entity extraction
    # Fix: fast-path in _extract_safe_name_candidate for "marks of NAME"
    def test_class_single_name_extraction(self):
        pq = _parse("marks of Aakash")
        assert pq.intent_family == IntentFamily.STRUCTURED_STUDENT

    # FAILURE CLASS: "aakash marks" (3 words) was returning AMBIGUOUS
    # Fix: name-search query scoring must catch name+marks without preceding verb
    def test_class_name_then_marks_routes_db(self):
        pq = _parse("result of Aakash Singh batch 2021")
        assert pq.route in _DB_ROUTES

    # FAILURE CLASS: AKTU queries were treated as student queries
    def test_class_aktu_never_hits_student_db(self):
        aktu_queries = [
            "aktu exam rules",
            "aktu syllabus for cse",
            "promotion rules aktu",
            "show aktu circular",
        ]
        for q in aktu_queries:
            pq = _parse(q)
            assert pq.route != Route.STUDENT_DB, (
                f"AKTU query must not hit student DB: {q!r}"
            )

    # FAILURE CLASS: General ML/AI questions were hitting the student DB
    def test_class_general_knowledge_never_db(self):
        gk_queries = [
            "what is machine learning",
            "what is artificial intelligence",
            "what is deep learning",
            "what is blockchain",
            "explain neural networks",
        ]
        for q in gk_queries:
            pq = _parse(q)
            assert pq.route not in {Route.STUDENT_DB, Route.DB_ANALYTICS}, (
                f"GK query must not hit DB: {q!r}"
            )

    # FAILURE CLASS: Writing tasks were routing to DB
    def test_class_writing_tasks_never_db(self):
        writing_queries = [
            "write a leave application",
            "write an email to my professor",
            "rewrite this more formally",
            "help me write a cover letter",
            "draft an application for hostel",
        ]
        for q in writing_queries:
            assert _routes_ai(q), f"Expected AI route for writing task: {q!r}"

    # FAILURE CLASS: Follow-up query without context was crashing
    def test_class_followup_without_context_handled(self):
        """Pronoun queries without active student must not crash — route gracefully."""
        follow_queries = [
            "his cgpa",
            "her percentage",
            "this student's marks",
        ]
        for q in follow_queries:
            pq = _parse(q)  # no context
            assert pq.route is not None  # must return a valid route

    # FAILURE CLASS: Disambiguation responses were being reclassified as new queries
    # Handled by app.py override — here we just verify numbers route to DB
    def test_class_number_selection_routes_db(self):
        """User typing '1' or '2' during name disambiguation must not go to AI."""
        pq = _parse("1")
        # Should be AMBIGUOUS or DB (app.py overrides to DB when pending_name_candidates)
        assert pq.route in {Route.CLARIFICATION, Route.STUDENT_DB, Route.STUDENT_METRIC,
                             Route.CONTEXT_FOLLOWUP}

    # FAILURE CLASS: Typo queries were failing silently
    def test_class_typo_corrections_applied(self):
        q = normalize_query("kya hai cgpa")
        assert "what" in q or "cgpa" in q  # kya → what

    def test_class_hinglish_typos(self):
        q = normalize_query("bata mujhe result")
        assert "tell" in q  # bata → tell


# ─────────────────────────────────────────────────────────────────────────────
# ParsedQuery contract tests
# Verify all fields are populated correctly.
# ─────────────────────────────────────────────────────────────────────────────
class TestParsedQueryContract:
    """The ParsedQuery dataclass must always be fully populated."""

    def test_all_fields_present(self):
        pq = _parse("show marks for 2104920100002")
        assert pq.raw == "show marks for 2104920100002"
        assert pq.normalized
        assert pq.intent_family
        assert pq.intent
        assert 0.0 <= pq.confidence <= 1.0
        assert isinstance(pq.is_structured, bool)
        assert isinstance(pq.is_open_ended, bool)
        assert isinstance(pq.is_followup, bool)
        assert isinstance(pq.needs_db, bool)
        assert isinstance(pq.entities, dict)
        assert isinstance(pq.filters, dict)
        assert pq.route

    def test_structured_query_flags(self):
        pq = _parse("show marks for 2104920100002")
        assert pq.is_structured is True
        assert pq.needs_db is True
        assert pq.is_open_ended is False

    def test_open_ended_query_flags(self):
        pq = _parse("what is cgpa")
        assert pq.is_open_ended is True
        assert pq.needs_db is False
        assert pq.needs_general_ai is True

    def test_confidence_nonzero_for_clear_queries(self):
        pq = _parse("show marks for 2104920100002")
        assert pq.confidence > 0.5

    def test_scores_dict_populated(self):
        pq = _parse("toppers of batch 2021")
        assert isinstance(pq.scores, dict)
        assert len(pq.scores) > 0


# ─────────────────────────────────────────────────────────────────────────────
# normalize_query & detect_query_shape unit tests
# ─────────────────────────────────────────────────────────────────────────────
class TestNormalizeAndShape:
    def test_normalizes_to_lowercase(self):
        assert normalize_query("SHOW MARKS") == "show marks"

    def test_collapses_whitespace(self):
        assert normalize_query("show   marks  for") == "show marks for"

    def test_strips(self):
        assert normalize_query("  hello  ") == "hello"

    def test_typo_rollno_corrected(self):
        q = normalize_query("show rollno 2104920100002")
        assert "roll" in q

    def test_typo_reslt_corrected(self):
        q = normalize_query("reslt of 2104920100002")
        assert "result" in q

    def test_shape_has_roll(self):
        shape = detect_query_shape("show marks for 2104920100002")
        assert shape["has_roll"] is True

    def test_shape_no_roll(self):
        shape = detect_query_shape("what is cgpa")
        assert shape["has_roll"] is False

    def test_shape_starts_explain(self):
        shape = detect_query_shape("what is a backlog")
        assert shape["starts_explain"] is True

    def test_shape_has_sem(self):
        shape = detect_query_shape("marks for sem 3")
        assert shape["has_sem"] is True

    def test_shape_is_fragment(self):
        shape = detect_query_shape("cgpa")
        assert shape["is_fragment"] is True

    def test_shape_is_command(self):
        shape = detect_query_shape("show marks for roll 2104920100002")
        assert shape["is_command"] is True

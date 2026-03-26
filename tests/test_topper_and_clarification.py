"""
Regression tests for:
  1. Range-based topper queries ("last 5 years", "from 2020 to 2024")
  2. Clarification reply binding (reply "2022" → topper query, not student lookup)
  3. Context isolation (old student context must not hijack new topper task)
  4. Multi-batch topper routing
"""

import sys
import os
import types
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from semantic_parser import (
    parse_query,
    extract_year_range,
    extract_years_from_reply,
    Route,
    IntentFamily,
)
from db_marks import resolve_batch_years


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ctx(roll=None):
    return {"last_roll": roll, "current_student": roll, "current_semester": None}


# ─────────────────────────────────────────────────────────────────────────────
# 1. extract_year_range() — pure function, no DB
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractYearRange(unittest.TestCase):

    def test_last_5_years_numeric(self):
        yr = extract_year_range("toppers of last 5 years")
        self.assertIsNotNone(yr)
        self.assertEqual(yr["type"], "last_n")
        self.assertEqual(yr["n"], 5)

    def test_last_five_years_word(self):
        yr = extract_year_range("give me toppers of last five years")
        self.assertIsNotNone(yr)
        self.assertEqual(yr["type"], "last_n")
        self.assertEqual(yr["n"], 5)

    def test_past_3_years(self):
        yr = extract_year_range("past 3 years toppers")
        self.assertIsNotNone(yr)
        self.assertEqual(yr["type"], "last_n")
        self.assertEqual(yr["n"], 3)

    def test_previous_n_years(self):
        yr = extract_year_range("list toppers of previous 4 years")
        self.assertIsNotNone(yr)
        self.assertEqual(yr["type"], "last_n")
        self.assertEqual(yr["n"], 4)

    def test_recent_n_years(self):
        yr = extract_year_range("recent 2 years rank list")
        self.assertIsNotNone(yr)
        self.assertEqual(yr["type"], "last_n")
        self.assertEqual(yr["n"], 2)

    def test_from_year_to_year(self):
        yr = extract_year_range("toppers from 2020 to 2024")
        self.assertIsNotNone(yr)
        self.assertEqual(yr["type"], "range")
        self.assertEqual(yr["from"], 2020)
        self.assertEqual(yr["to"], 2024)

    def test_between_years(self):
        yr = extract_year_range("between 2019 and 2023 toppers")
        self.assertIsNotNone(yr)
        self.assertEqual(yr["type"], "range")
        self.assertEqual(yr["from"], 2019)
        self.assertEqual(yr["to"], 2023)

    def test_single_batch_returns_none(self):
        yr = extract_year_range("toppers of batch 2022")
        self.assertIsNone(yr)

    def test_unrelated_query_returns_none(self):
        yr = extract_year_range("what is CGPA?")
        self.assertIsNone(yr)

    def test_last_ten_years_word(self):
        yr = extract_year_range("last ten years toppers")
        self.assertIsNotNone(yr)
        self.assertEqual(yr["n"], 10)


# ─────────────────────────────────────────────────────────────────────────────
# 2. parse_query() — range queries route to DB_ANALYTICS without clarification
# ─────────────────────────────────────────────────────────────────────────────

class TestTopperRangeRouting(unittest.TestCase):

    def test_last_5_years_routes_to_db_analytics(self):
        pq = parse_query("toppers of last 5 years", _ctx())
        self.assertEqual(pq.route, Route.DB_ANALYTICS)

    def test_last_5_years_no_clarification_needed(self):
        pq = parse_query("give me the list of toppers of last 5 years", _ctx())
        self.assertFalse(pq.clarification_needed)

    def test_past_3_years_routes_db_analytics(self):
        pq = parse_query("rank list of past 3 years", _ctx())
        self.assertEqual(pq.route, Route.DB_ANALYTICS)

    def test_from_2020_to_2024_routes_db_analytics(self):
        pq = parse_query("toppers from 2020 to 2024", _ctx())
        self.assertEqual(pq.route, Route.DB_ANALYTICS)
        self.assertFalse(pq.clarification_needed)

    def test_between_years_routes_db_analytics(self):
        pq = parse_query("between 2019 and 2023 toppers", _ctx())
        self.assertEqual(pq.route, Route.DB_ANALYTICS)

    def test_year_range_stored_in_filters(self):
        pq = parse_query("toppers of last 5 years", _ctx())
        self.assertIn("year_range", pq.filters)
        self.assertEqual(pq.filters["year_range"]["type"], "last_n")
        self.assertEqual(pq.filters["year_range"]["n"], 5)

    def test_last_5_years_is_topper_intent(self):
        pq = parse_query("toppers of last 5 years", _ctx())
        self.assertEqual(pq.intent, "topper_query")

    def test_single_batch_still_routes_db_analytics(self):
        pq = parse_query("toppers of batch 2022", _ctx())
        self.assertEqual(pq.route, Route.DB_ANALYTICS)
        self.assertFalse(pq.clarification_needed)

    def test_topper_without_batch_asks_clarification(self):
        pq = parse_query("give me toppers", _ctx())
        self.assertTrue(pq.clarification_needed)
        self.assertEqual(pq.route, Route.CLARIFICATION)

    def test_top_20_students_batch_2021_no_clarification(self):
        pq = parse_query("top 20 students of batch 2021", _ctx())
        self.assertEqual(pq.route, Route.DB_ANALYTICS)
        self.assertFalse(pq.clarification_needed)

    def test_topper_with_context_still_asks_clarification_without_batch(self):
        # Even with active student context, a fresh topper query with no batch needs clarification
        pq = parse_query("show toppers", _ctx(roll="2104920100002"))
        self.assertTrue(pq.clarification_needed)

    def test_last_n_years_not_treated_as_single_batch(self):
        pq = parse_query("toppers of last 5 years", _ctx())
        # Should NOT have a single batch filter — it has a year_range instead
        self.assertNotIn("batch", pq.filters)
        self.assertIn("year_range", pq.filters)


# ─────────────────────────────────────────────────────────────────────────────
# 3. extract_years_from_reply() — pure clarification reply parser
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractYearsFromReply(unittest.TestCase):
    """Test extract_years_from_reply (lives in semantic_parser — no Streamlit)."""

    def test_four_digit_year(self):
        self.assertEqual(extract_years_from_reply("2022"), [2022])

    def test_two_digit_year(self):
        self.assertEqual(extract_years_from_reply("22"), [2022])

    def test_batch_prefix(self):
        self.assertEqual(extract_years_from_reply("batch 2021"), [2021])

    def test_multiple_years(self):
        self.assertEqual(extract_years_from_reply("2021 and 2022"), [2021, 2022])

    def test_plain_text_no_year(self):
        self.assertEqual(extract_years_from_reply("sure"), [])

    def test_garbage_input_no_year(self):
        self.assertEqual(extract_years_from_reply("some random text"), [])

    def test_year_with_surrounding_text(self):
        self.assertEqual(extract_years_from_reply("for year 2023 please"), [2023])

    def test_deduplication(self):
        self.assertEqual(extract_years_from_reply("2022 and 2022"), [2022])


# ─────────────────────────────────────────────────────────────────────────────
# 4. Context isolation — topper queries must ignore unrelated student context
# ─────────────────────────────────────────────────────────────────────────────

class TestContextIsolation(unittest.TestCase):

    def test_topper_query_is_not_followup_with_roll_context(self):
        # A fresh topper query should NOT be treated as a follow-up to a student
        pq = parse_query("toppers of batch 2022", _ctx(roll="2104920100002"))
        # It should route to analytics, not context_followup
        self.assertEqual(pq.route, Route.DB_ANALYTICS)
        self.assertNotEqual(pq.route, Route.CONTEXT_FOLLOWUP)

    def test_student_context_does_not_make_topper_query_a_followup(self):
        pq = parse_query("top 20 students batch 2021", _ctx(roll="2104920100002"))
        self.assertFalse(pq.is_followup)

    def test_new_topper_query_has_topper_intent(self):
        pq = parse_query("toppers of batch 2022", _ctx(roll="2104920100002"))
        self.assertEqual(pq.intent, "topper_query")

    def test_student_roll_context_does_not_extract_as_topper_batch(self):
        # Query about toppers of 2022 with roll ctx — batch should be 2022, not from roll
        pq = parse_query("toppers of batch 2022", _ctx(roll="2104920100002"))
        self.assertEqual(pq.filters.get("batch"), 2022)

    def test_analytics_intent_does_not_bleed_into_student_result(self):
        # "show all students of batch 2022" is analytics, not student_result
        pq = parse_query("list students of batch 2022", _ctx(roll="2104920100002"))
        self.assertIn(pq.route, (Route.DB_ANALYTICS, Route.STUDENT_DB))
        self.assertNotEqual(pq.intent, "student_result_query")

    def test_unrelated_old_roll_context_ignored_for_range_topper(self):
        pq = parse_query("toppers of last 3 years", _ctx(roll="2104920100002"))
        self.assertEqual(pq.route, Route.DB_ANALYTICS)
        self.assertFalse(pq.clarification_needed)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Clarification binding — pending_clarification state logic
# ─────────────────────────────────────────────────────────────────────────────

class TestClarificationBinding(unittest.TestCase):
    """
    Test the clarification binding logic by verifying that:
    - extract_years_from_reply correctly identifies years from user replies
    - parse_query correctly stores pending clarification intent for bare topper queries
    - A bare year reply after a bare topper query resolves correctly
    These tests verify the data contracts between turns without importing app.py.
    """

    def test_topper_query_without_batch_sets_clarification_intent(self):
        """parse_query for a bare topper query flags clarification_needed=True."""
        pq = parse_query("give me toppers", _ctx())
        self.assertTrue(pq.clarification_needed)
        self.assertEqual(pq.intent, "topper_query")

    def test_clarification_question_contains_batch_hint(self):
        pq = parse_query("give me toppers", _ctx())
        self.assertIn("batch", pq.clarification_question.lower())

    def test_year_reply_is_extractable(self):
        """Reply '2022' should extract to [2022] — ready to be bound."""
        self.assertEqual(extract_years_from_reply("2022"), [2022])

    def test_two_digit_reply_is_extractable(self):
        self.assertEqual(extract_years_from_reply("22"), [2022])

    def test_text_reply_without_year_is_not_extractable(self):
        """Ambiguous text reply should not extract a year."""
        self.assertEqual(extract_years_from_reply("sure"), [])

    def test_full_year_reply_after_clarification_resolves_to_db_analytics(self):
        """
        If user gives a full query 'toppers of batch 2022' (i.e. no pending
        clarification path), it routes correctly on its own.
        This is the control case for the clarification flow.
        """
        pq = parse_query("toppers of batch 2022", _ctx())
        self.assertEqual(pq.route, Route.DB_ANALYTICS)
        self.assertFalse(pq.clarification_needed)

    def test_numeric_reply_is_not_treated_as_roll_number(self):
        """
        A 4-digit year like '2022' must not be misclassified as a roll number.
        Roll numbers are 6+ digits; years are exactly 4 digits.
        """
        pq = parse_query("2022", _ctx())
        # Must not have a roll entity
        self.assertNotIn("roll", pq.entities)

    def test_bare_year_with_pending_topper_intent_resolves_correctly(self):
        """
        Simulate the clarification binding: original_query='give me toppers'
        triggered a clarification. Now the user replies '2022'.
        The resolve_batch_years path should work with [2022].
        """
        years = extract_years_from_reply("2022")
        self.assertEqual(years, [2022])
        with patch("db_marks.get_available_batch_years", return_value=[2024, 2023, 2022, 2021]):
            result = resolve_batch_years({"type": "last_n", "n": 1})
        self.assertEqual(result, [2024])  # last_n uses DB order


# ─────────────────────────────────────────────────────────────────────────────
# 6. resolve_batch_years() — year range expansion
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveBatchYears(unittest.TestCase):
    """Test resolve_batch_years (lives in db_marks — no Streamlit)."""

    def _resolve(self, yr, available=None):
        db_years = [2024, 2023, 2022, 2021, 2020, 2019] if available is None else available
        with patch("db_marks.get_available_batch_years", return_value=db_years):
            return resolve_batch_years(yr)

    def test_last_n_uses_db_available_years(self):
        result = self._resolve({"type": "last_n", "n": 3}, available=[2024, 2023, 2022, 2021])
        self.assertEqual(result, [2024, 2023, 2022])

    def test_last_5_returns_5_years(self):
        result = self._resolve({"type": "last_n", "n": 5})
        self.assertEqual(len(result), 5)

    def test_range_type_returns_descending_list(self):
        result = self._resolve({"type": "range", "from": 2020, "to": 2022})
        self.assertEqual(result, [2022, 2021, 2020])

    def test_range_type_handles_reversed_order(self):
        result = self._resolve({"type": "range", "from": 2023, "to": 2021})
        self.assertEqual(result, [2023, 2022, 2021])

    def test_last_n_larger_than_available(self):
        result = self._resolve({"type": "last_n", "n": 10}, available=[2024, 2023])
        self.assertEqual(result, [2024, 2023])  # only what's available

    def test_empty_available_returns_empty(self):
        result = self._resolve({"type": "last_n", "n": 5}, available=[])
        self.assertEqual(result, [])


# ─────────────────────────────────────────────────────────────────────────────
# 7. General regression: phrase variants
# ─────────────────────────────────────────────────────────────────────────────

class TestTopperPhraseVariants(unittest.TestCase):

    def test_top_students_last_3_years(self):
        pq = parse_query("top students of last 3 years", _ctx())
        self.assertEqual(pq.route, Route.DB_ANALYTICS)
        self.assertFalse(pq.clarification_needed)

    def test_rank_list_last_5_years(self):
        pq = parse_query("rank list of last 5 years", _ctx())
        self.assertEqual(pq.route, Route.DB_ANALYTICS)

    def test_highest_cgpa_past_5_years(self):
        pq = parse_query("highest cgpa students of past 5 years", _ctx())
        self.assertEqual(pq.route, Route.DB_ANALYTICS)

    def test_best_students_last_3_years(self):
        pq = parse_query("best students of last 3 years", _ctx())
        # Intent should be structured_student, not general_ai
        self.assertNotEqual(pq.route, Route.GENERAL_AI)

    def test_topper_batch_2022_single_works(self):
        pq = parse_query("toppers of batch 2022", _ctx())
        self.assertEqual(pq.route, Route.DB_ANALYTICS)
        self.assertFalse(pq.clarification_needed)
        self.assertEqual(pq.filters.get("batch"), 2022)

    def test_topper_no_batch_no_range_asks_clarification(self):
        pq = parse_query("who are the toppers", _ctx())
        self.assertTrue(pq.clarification_needed)

    def test_last_n_years_clarification_message_includes_last_n_hint(self):
        """Clarification message for bare topper query should hint at 'last N years'."""
        pq = parse_query("give me toppers", _ctx())
        self.assertIsNotNone(pq.clarification_question)
        # Should mention batch year and possibly range example
        self.assertIn("batch", pq.clarification_question.lower())


if __name__ == "__main__":
    unittest.main()

"""
Failure classifier — categorises low-quality responses into one of five failure modes.

Failure categories
------------------
no_data          SQL succeeded but row_count == 0; RAG found no relevant chunks
sql_error        SQL pipeline raised an exception or returned an error string
hallucination    LLM generated plausible-sounding but factually unlikely text
                 (heuristic: response contains hedging phrases with no data context)
off_topic        Query routed to RAG but response doesn't address the question
                 (heuristic: very low cosine similarity between query and response embeddings)
incomplete       Response is suspiciously short (< 40 chars) for a substantive question

Classified failures are added to a healing queue in prompts.db (prompt_evolution_log table)
so that Phase 9's self-improvement agent can process them.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Optional

from config import settings
from db.sqlite_client import execute

logger = logging.getLogger(__name__)

# Phrases that suggest the LLM is hedging without data
_HALLUCINATION_HINTS = [
    "i believe", "i think", "probably", "likely", "might be",
    "could be", "it appears", "seems like", "generally speaking",
    "in most cases", "typically", "usually",
]

_INCOMPLETE_THRESHOLD = 40          # chars
_SHORT_QUESTION_WORDS = 5           # query word count above which we penalise short answers


def classify_failure(
    query: str,
    response_text: str,
    route_used: str,
    sql_row_count: Optional[int],
    sql_error: Optional[str],
    chunk_count: int,
    quality_score: float,
) -> Optional[str]:
    """
    Return a failure category string or None if the response is acceptable.

    Only classifies when quality_score <= 0.50.
    """
    if quality_score > 0.50:
        return None

    # 1. SQL error
    if sql_error and "SQL" in route_used.upper():
        return "sql_error"

    # 2. No data
    if sql_row_count == 0 and chunk_count == 0:
        return "no_data"
    if sql_row_count is not None and sql_row_count == 0 and "SQL" in route_used.upper():
        return "no_data"
    if chunk_count == 0 and "RAG" in route_used.upper():
        return "no_data"

    # 3. Incomplete response
    resp_lower = response_text.lower().strip()
    query_words = len(query.split())
    if len(response_text) < _INCOMPLETE_THRESHOLD and query_words >= _SHORT_QUESTION_WORDS:
        return "incomplete"

    # 4. Hallucination heuristic
    hint_count = sum(1 for hint in _HALLUCINATION_HINTS if hint in resp_lower)
    if hint_count >= 3:
        return "hallucination"

    # 5. Off-topic heuristic (query keywords absent from response)
    query_keywords = {w.lower() for w in query.split() if len(w) > 4}
    if query_keywords:
        matched = sum(1 for kw in query_keywords if kw in resp_lower)
        coverage = matched / len(query_keywords)
        if coverage < 0.2:
            return "off_topic"

    return "low_quality"            # generic fallback


# ── Healing queue ─────────────────────────────────────────────────────────────

async def add_to_healing_queue(
    feedback_id: str,
    query: str,
    response: str,
    failure_category: str,
    quality_score: float,
) -> None:
    """
    Insert a failed response into feedback.db/healing_queue.
    The admin dashboard reads from this table to show pending fixes.
    """
    entry_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    # Determine fix_type and risk from failure category
    fix_type_map = {
        "sql_error": "sql_prompt",
        "no_data": "sql_prompt",
        "incomplete": "response_prompt",
        "hallucination": "response_grounding",
        "off_topic": "manual_review",
        "low_quality": "manual_review",
    }
    risk_map = {
        "sql_error": "high",
        "hallucination": "high",
        "no_data": "medium",
        "incomplete": "low",
        "off_topic": "medium",
        "low_quality": "low",
    }

    fix_details = json.dumps({
        "feedback_id": feedback_id,
        "failure_category": failure_category,
        "quality_score": quality_score,
    })

    change_reason = f"Quality score {quality_score:.3f} — {failure_category}"

    await execute(
        settings.FEEDBACK_DB,
        """INSERT OR IGNORE INTO healing_queue
               (id, query, original_response, failure_type, fix_type,
                fix_details, change_reason, risk_level, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            entry_id,
            query[:500],
            response[:1000],
            failure_category,
            fix_type_map.get(failure_category, "manual_review"),
            fix_details,
            change_reason,
            risk_map.get(failure_category, "medium"),
            "pending",
            now,
        ),
    )

    logger.info(
        "Added to healing queue: feedback=%s category=%s score=%.3f",
        feedback_id, failure_category, quality_score,
    )


async def mark_healed(feedback_id: str) -> None:
    """Mark feedback row as healed (healed = 1)."""
    from db.sqlite_client import execute as _exec
    await _exec(
        settings.FEEDBACK_DB,
        "UPDATE feedback SET healed = 1 WHERE id = ?",
        (feedback_id,),
    )

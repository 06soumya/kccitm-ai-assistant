"""
Feedback collector — writes explicit and implicit signals to feedback.db.

Explicit:  User submits a thumbs-up/down rating via POST /feedback.
Implicit:  Orchestrator detects behavioural patterns and calls record_implicit_signal().

Implicit signal types
---------------------
rephrase      — user sends a very similar query shortly after the last one
                 (edit distance < 50 % of original length)
follow_up     — user asks a short clarifying question within 90 seconds of the
                 last assistant reply (suggests the answer was incomplete)
quick_exit    — session ends < 30 seconds after an assistant reply (suggests
                 dissatisfaction or a trivially short answer)
long_session  — session has ≥ 6 turns; positive signal (user stayed engaged)
"""

import json
import logging
import time
import uuid
from datetime import datetime

from config import settings
from db.sqlite_client import execute, fetch_one

logger = logging.getLogger(__name__)


# ── Explicit feedback ─────────────────────────────────────────────────────────

async def record_explicit_feedback(
    message_id: str,
    session_id: str,
    rating: int,
    query_text: str = "",
    response_text: str = "",
    route_used: str = "",
    sql_generated: str | None = None,
    chunks_used: list | None = None,
    reranker_scores: list | None = None,
    confidence_score: float | None = None,
    feedback_text: str | None = None,
) -> str:
    """
    Insert or update an explicit feedback row.

    Returns feedback_id.
    """
    feedback_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    chunks_json = json.dumps(chunks_used or [])
    scores_json = json.dumps(reranker_scores or [])

    await execute(
        settings.FEEDBACK_DB,
        """INSERT INTO feedback
               (id, message_id, session_id, query_text, response_text,
                rating, feedback_text, route_used, sql_generated,
                chunks_used, reranker_scores, confidence_score, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            feedback_id, message_id, session_id, query_text, response_text,
            rating, feedback_text, route_used, sql_generated,
            chunks_json, scores_json, confidence_score, now,
        ),
    )

    logger.debug("Explicit feedback recorded: %s (rating=%s)", feedback_id, rating)
    return feedback_id


# ── Implicit signal detection ─────────────────────────────────────────────────

def _edit_distance_ratio(a: str, b: str) -> float:
    """
    Levenshtein distance / max(len(a), len(b)).
    Returns 0.0 (identical) … 1.0 (completely different).
    Quick O(n*m) implementation; fine for short query strings.
    """
    a, b = a.lower().strip(), b.lower().strip()
    if a == b:
        return 0.0
    la, lb = len(a), len(b)
    if la == 0 or lb == 0:
        return 1.0

    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = curr

    return prev[lb] / max(la, lb)


def detect_implicit_signals(
    current_query: str,
    previous_query: str | None,
    time_gap_seconds: float,
    session_turn_count: int,
) -> list[str]:
    """
    Return a list of implicit signal types detected for this turn.
    """
    signals = []

    # Long session — positive
    if session_turn_count >= 6:
        signals.append("long_session")

    # Rephrase — very similar query sent quickly
    if previous_query and time_gap_seconds < 120:
        ratio = _edit_distance_ratio(current_query, previous_query)
        if ratio < 0.4:
            signals.append("rephrase")

    # Follow-up — short question asked quickly
    words = len(current_query.split())
    if words <= 8 and previous_query and time_gap_seconds < 90:
        signals.append("follow_up")

    return signals


async def record_implicit_signal(
    session_id: str,
    signal_type: str,
    original_query: str,
    follow_up_query: str,
    time_gap_seconds: float,
) -> None:
    """Persist one implicit signal row."""
    signal_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    await execute(
        settings.FEEDBACK_DB,
        """INSERT INTO implicit_signals
               (id, session_id, signal_type, original_query,
                follow_up_query, time_gap_seconds, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (signal_id, session_id, signal_type, original_query,
         follow_up_query, time_gap_seconds, now),
    )

    logger.debug("Implicit signal recorded: %s (%s)", signal_type, session_id)


# ── Chunk analytics ───────────────────────────────────────────────────────────

async def update_chunk_analytics(chunk_ids: list[str]) -> None:
    """Increment times_retrieved for each chunk that appeared in a response."""
    if not chunk_ids:
        return
    for chunk_id in chunk_ids:
        existing = await fetch_one(
            settings.FEEDBACK_DB,
            "SELECT chunk_id FROM chunk_analytics WHERE chunk_id = ?",
            (chunk_id,),
        )
        if existing:
            await execute(
                settings.FEEDBACK_DB,
                "UPDATE chunk_analytics SET times_retrieved = times_retrieved + 1 WHERE chunk_id = ?",
                (chunk_id,),
            )
        else:
            await execute(
                settings.FEEDBACK_DB,
                "INSERT INTO chunk_analytics (chunk_id, times_retrieved) VALUES (?, 1)",
                (chunk_id,),
            )

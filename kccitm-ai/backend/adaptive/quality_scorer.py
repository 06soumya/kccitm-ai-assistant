"""
Quality scorer — derives a composite 0-1 quality score for every assistant response.

Score components
----------------
explicit_rating   0.0 – 1.0   User thumbs rating normalised from 1-5 scale (weight 0.40)
response_length   0.0 – 1.0   Penalise very short (<40 chars) and very long (>2000 chars) (weight 0.15)
confidence        0.0 – 1.0   Router confidence passed through from RouteResult (weight 0.20)
no_implicit_neg   0.0 / 1.0   0.0 if rephrase/follow_up implicit signal seen, else 1.0 (weight 0.25)

If no explicit rating is available, explicit_rating component is distributed equally among the
remaining three, keeping their relative weights.

The final score is written back to the feedback row via update_quality_score().
"""

import logging
from typing import Optional

from config import settings
from db.sqlite_client import execute, fetch_one

logger = logging.getLogger(__name__)

# Component weights (must sum to 1.0)
_W_EXPLICIT   = 0.40
_W_LENGTH     = 0.15
_W_CONFIDENCE = 0.20
_W_NO_NEG     = 0.25


def _rating_score(rating: Optional[int]) -> Optional[float]:
    """Convert 1-5 star rating to 0-1."""
    if rating is None:
        return None
    return (max(1, min(5, rating)) - 1) / 4.0


def _length_score(response_text: str) -> float:
    length = len(response_text)
    if length < 40:
        return 0.2
    if length > 2000:
        return 0.7          # penalise rambling slightly
    # sweet spot 100-800 chars
    if 100 <= length <= 800:
        return 1.0
    if length < 100:
        return 0.5 + 0.5 * (length - 40) / 60
    return 0.7 + 0.3 * (2000 - length) / 1200


def _no_negative_signal_score(implicit_signals: list[str]) -> float:
    """1.0 if no negative implicit signals, 0.0 if rephrase/follow_up detected."""
    negative = {"rephrase", "follow_up"}
    if any(s in negative for s in implicit_signals):
        return 0.0
    return 1.0


def compute_quality_score(
    response_text: str,
    rating: Optional[int],
    confidence_score: Optional[float],
    implicit_signals: list[str],
) -> float:
    """
    Return composite quality score in [0, 1].
    """
    rs = _rating_score(rating)
    ls = _length_score(response_text)
    cs = confidence_score if confidence_score is not None else 0.5
    ns = _no_negative_signal_score(implicit_signals)

    if rs is None:
        # Redistribute explicit weight evenly to the other three
        total_w = _W_LENGTH + _W_CONFIDENCE + _W_NO_NEG
        score = (ls * _W_LENGTH + cs * _W_CONFIDENCE + ns * _W_NO_NEG) / total_w
    else:
        score = (
            rs * _W_EXPLICIT
            + ls * _W_LENGTH
            + cs * _W_CONFIDENCE
            + ns * _W_NO_NEG
        )

    return round(max(0.0, min(1.0, score)), 4)


# ── Persistence ───────────────────────────────────────────────────────────────

async def update_quality_score(feedback_id: str, quality_score: float) -> None:
    """Write quality_score back to the feedback row."""
    await execute(
        settings.FEEDBACK_DB,
        "UPDATE feedback SET quality_score = ? WHERE id = ?",
        (quality_score, feedback_id),
    )
    logger.debug("Quality score %.3f written to feedback %s", quality_score, feedback_id)


async def score_feedback_row(feedback_id: str) -> float:
    """
    Load a feedback row, compute its quality score, persist it, and return it.
    Useful for batch re-scoring.
    """
    row = await fetch_one(
        settings.FEEDBACK_DB,
        """SELECT response_text, rating, confidence_score, implicit_signals
             FROM feedback WHERE id = ?""",
        (feedback_id,),
    )
    if not row:
        logger.warning("Feedback row %s not found", feedback_id)
        return 0.0

    import json
    response_text = row["response_text"] or ""
    rating        = row["rating"]
    confidence    = row["confidence_score"]
    try:
        signals = json.loads(row["implicit_signals"] or "[]")
    except (TypeError, ValueError):
        signals = []

    score = compute_quality_score(response_text, rating, confidence, signals)
    await update_quality_score(feedback_id, score)
    return score

"""
Feedback routes — Phase 8 full implementation.

POST /feedback     Submit explicit rating + optional text.
                   Triggers quality scoring and failure classification.
                   Severe failures (score < 0.35) are healed immediately.
GET  /feedback/stats   Aggregate stats for the dashboard.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field

from adaptive.failure_classifier import add_to_healing_queue, classify_failure
from adaptive.feedback_collector import (
    record_explicit_feedback,
    update_chunk_analytics,
)
from adaptive.quality_scorer import compute_quality_score, update_quality_score
from api.middleware.auth import TokenData, get_current_user
from config import settings
from db.sqlite_client import execute, fetch_all, fetch_one

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Request / response models ─────────────────────────────────────────────────

class FeedbackRequest(BaseModel):
    message_id: str
    session_id: str
    rating: int = Field(..., ge=1, le=5)    # 1 = very bad, 5 = excellent
    feedback_text: Optional[str] = None
    # Optional rich metadata — populated by the chat route if available
    query_text: Optional[str] = None
    response_text: Optional[str] = None
    route_used: Optional[str] = None
    sql_generated: Optional[str] = None
    chunks_used: Optional[list] = None
    reranker_scores: Optional[list] = None
    confidence_score: Optional[float] = None


class FeedbackResponse(BaseModel):
    feedback_id: str
    quality_score: float
    failure_category: Optional[str] = None
    healed: bool = False
    healed_response: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    req: FeedbackRequest,
    background_tasks: BackgroundTasks,
    current_user: TokenData = Depends(get_current_user),
):
    """
    Submit feedback on an assistant response.

    1. Persist the explicit rating.
    2. Compute composite quality score.
    3. Classify failure mode (if low quality).
    4. Add to healing queue (if quality < 0.50).
    5. Attempt immediate healing (if quality < 0.35).
    6. Update chunk analytics in background.
    """
    # 0. Look up query_text and response_text from session messages if not provided
    #    NOTE: Frontend generates temp IDs like "msg_1234567890" that don't match
    #    the UUID IDs stored in sessions.db. So we look up the most recent
    #    assistant+user message pair in the session instead.
    if not req.query_text or not req.response_text:
        try:
            # Get the most recent assistant message in this session
            assistant_msg = await fetch_one(
                settings.SESSION_DB,
                """SELECT id, content, metadata, created_at FROM messages
                   WHERE session_id = ? AND role = 'assistant'
                   ORDER BY created_at DESC LIMIT 1""",
                (req.session_id,),
            )
            if assistant_msg:
                req.response_text = req.response_text or assistant_msg["content"]
                # Get the user message right before it (the query)
                user_msg = await fetch_one(
                    settings.SESSION_DB,
                    """SELECT content FROM messages
                       WHERE session_id = ? AND role = 'user'
                         AND created_at <= ?
                       ORDER BY created_at DESC LIMIT 1""",
                    (req.session_id, assistant_msg["created_at"]),
                )
                if user_msg:
                    req.query_text = req.query_text or user_msg["content"]
                # Also get route_used from message metadata
                if not req.route_used:
                    try:
                        meta = json.loads(assistant_msg.get("metadata") or "{}")
                        req.route_used = req.route_used or meta.get("route_used", "")
                    except (json.JSONDecodeError, TypeError):
                        pass
        except Exception as exc:
            logger.debug("Could not look up message context: %s", exc)

    # 1. Persist explicit feedback
    feedback_id = await record_explicit_feedback(
        message_id=req.message_id,
        session_id=req.session_id,
        rating=req.rating,
        query_text=req.query_text or "",
        response_text=req.response_text or "",
        route_used=req.route_used or "",
        sql_generated=req.sql_generated,
        chunks_used=req.chunks_used,
        reranker_scores=req.reranker_scores,
        confidence_score=req.confidence_score,
        feedback_text=req.feedback_text,
    )

    # 2. Compute quality score
    quality_score = compute_quality_score(
        response_text=req.response_text or "",
        rating=req.rating,
        confidence_score=req.confidence_score,
        implicit_signals=[],     # explicit path — no implicit signals yet
    )
    await update_quality_score(feedback_id, quality_score)

    # 3. Classify failure
    failure_category = classify_failure(
        query=req.query_text or "",
        response_text=req.response_text or "",
        route_used=req.route_used or "",
        sql_row_count=None,
        sql_error=None,
        chunk_count=len(req.chunks_used or []),
        quality_score=quality_score,
    )

    # 4. Add to healing queue for moderate failures
    if failure_category and quality_score < 0.50:
        background_tasks.add_task(
            add_to_healing_queue,
            feedback_id=feedback_id,
            query=req.query_text or "",
            response=req.response_text or "",
            failure_category=failure_category,
            quality_score=quality_score,
        )

    # 5. Immediate healing for severe failures
    healed = False
    healed_response = None
    if failure_category and quality_score < 0.35 and req.query_text:
        try:
            from adaptive.query_healer import heal_query
            healed_response = await heal_query(
                query=req.query_text,
                failure_category=failure_category,
                original_response=req.response_text or "",
                session_id=req.session_id,
            )
            if healed_response:
                healed = True
                await execute(
                    settings.FEEDBACK_DB,
                    "UPDATE feedback SET healed = 1 WHERE id = ?",
                    (feedback_id,),
                )
        except Exception as exc:
            logger.warning("Immediate healing failed for %s: %s", feedback_id, exc)

    # 6. Background: update chunk analytics
    if req.chunks_used:
        background_tasks.add_task(update_chunk_analytics, req.chunks_used)

    return FeedbackResponse(
        feedback_id=feedback_id,
        quality_score=quality_score,
        failure_category=failure_category,
        healed=healed,
        healed_response=healed_response,
    )


@router.get("/feedback/stats")
async def feedback_stats(
    current_user: TokenData = Depends(get_current_user),
):
    """Aggregate feedback statistics."""
    rows = await fetch_all(
        settings.FEEDBACK_DB,
        """SELECT
               COUNT(*)                            AS total,
               AVG(rating)                         AS avg_rating,
               AVG(quality_score)                  AS avg_quality,
               SUM(CASE WHEN rating >= 4 THEN 1 ELSE 0 END) AS positive,
               SUM(CASE WHEN rating <= 2 THEN 1 ELSE 0 END) AS negative,
               SUM(CASE WHEN healed = 1  THEN 1 ELSE 0 END) AS healed
           FROM feedback""",
    )
    row = rows[0] if rows else {}

    pending_healing = await fetch_all(
        settings.PROMPTS_DB,
        """SELECT COUNT(*) AS cnt FROM prompt_evolution_log
           WHERE prompt_name = 'healing_queue'
             AND (new_version = '' OR new_version IS NULL)""",
    )
    pending = pending_healing[0].get("cnt", 0) if pending_healing else 0

    return {
        "total_feedback": row.get("total", 0) or 0,
        "avg_rating": round(float(row.get("avg_rating") or 0), 2),
        "avg_quality_score": round(float(row.get("avg_quality") or 0), 4),
        "positive_feedback": row.get("positive", 0) or 0,
        "negative_feedback": row.get("negative", 0) or 0,
        "healed_responses": row.get("healed", 0) or 0,
        "pending_healing": pending,
    }

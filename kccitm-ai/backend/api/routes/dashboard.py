"""
Dashboard routes — Phase 8 quality & feedback analytics.

All endpoints require admin role.

GET /admin/dashboard/quality     Quality score distribution + trends
GET /admin/dashboard/failures    Failure category breakdown
GET /admin/dashboard/healing     Healing queue status
GET /admin/dashboard/chunks      Top retrieved / highest-rated chunks
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.middleware.auth import TokenData, require_admin
from config import settings
from db.sqlite_client import execute, fetch_all, fetch_one

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/dashboard/feedback")
async def feedback_list(
    limit: int = Query(50, ge=1, le=200),
    _: TokenData = Depends(require_admin),
):
    """Recent feedback entries for admin review."""
    rows = await fetch_all(
        settings.FEEDBACK_DB,
        """SELECT id, query_text, response_text, rating, quality_score,
                  route_used, feedback_text, created_at
           FROM feedback
           ORDER BY created_at DESC
           LIMIT ?""",
        (limit,),
    )
    return {
        "feedback": [
            {
                "id": r["id"],
                "query_text": r.get("query_text", ""),
                "response_text": r.get("response_text", ""),
                "rating": r.get("rating"),
                "quality_score": r.get("quality_score"),
                "route_used": r.get("route_used", ""),
                "feedback_text": r.get("feedback_text", ""),
                "created_at": r.get("created_at", ""),
            }
            for r in rows
        ]
    }


@router.get("/dashboard/signals")
async def implicit_signals(
    limit: int = Query(50, ge=1, le=200),
    _: TokenData = Depends(require_admin),
):
    """Implicit signals detected from user behavior."""
    rows = await fetch_all(
        settings.FEEDBACK_DB,
        """SELECT id, session_id, signal_type, original_query, follow_up_query, created_at
           FROM implicit_signals
           ORDER BY created_at DESC
           LIMIT ?""",
        (limit,),
    )
    return {
        "signals": [
            {
                "id": r["id"],
                "signal_type": r.get("signal_type", ""),
                "original_query": r.get("original_query", ""),
                "follow_up_query": r.get("follow_up_query", ""),
                "created_at": r.get("created_at", ""),
            }
            for r in rows
        ]
    }


@router.get("/dashboard/quality")
async def quality_dashboard(
    days: int = Query(7, ge=1, le=90),
    _: TokenData = Depends(require_admin),
):
    """
    Quality score distribution for the last N days.

    Returns:
    - score_distribution: counts in bands [0-0.2), [0.2-0.4), [0.4-0.6), [0.6-0.8), [0.8-1.0]
    - daily_avg: average quality score per day
    - route_quality: avg quality score per route
    """
    # Score distribution
    dist_rows = await fetch_all(
        settings.FEEDBACK_DB,
        """SELECT
               SUM(CASE WHEN quality_score < 0.2  THEN 1 ELSE 0 END) AS very_low,
               SUM(CASE WHEN quality_score >= 0.2 AND quality_score < 0.4 THEN 1 ELSE 0 END) AS low,
               SUM(CASE WHEN quality_score >= 0.4 AND quality_score < 0.6 THEN 1 ELSE 0 END) AS medium,
               SUM(CASE WHEN quality_score >= 0.6 AND quality_score < 0.8 THEN 1 ELSE 0 END) AS high,
               SUM(CASE WHEN quality_score >= 0.8 THEN 1 ELSE 0 END) AS very_high,
               COUNT(*) AS total,
               AVG(quality_score) AS overall_avg
           FROM feedback
           WHERE created_at >= datetime('now', ?)""",
        (f"-{days} days",),
    )
    dist = dist_rows[0] if dist_rows else {}

    # Daily average quality
    daily_rows = await fetch_all(
        settings.FEEDBACK_DB,
        """SELECT
               date(created_at) AS day,
               AVG(quality_score) AS avg_quality,
               COUNT(*) AS count
           FROM feedback
           WHERE created_at >= datetime('now', ?)
           GROUP BY date(created_at)
           ORDER BY day""",
        (f"-{days} days",),
    )

    # Per-route quality
    route_rows = await fetch_all(
        settings.FEEDBACK_DB,
        """SELECT
               route_used,
               AVG(quality_score) AS avg_quality,
               COUNT(*) AS count
           FROM feedback
           WHERE created_at >= datetime('now', ?)
             AND route_used IS NOT NULL AND route_used != ''
           GROUP BY route_used
           ORDER BY avg_quality DESC""",
        (f"-{days} days",),
    )

    return {
        "period_days": days,
        "score_distribution": {
            "very_low":  int(dist.get("very_low",  0) or 0),
            "low":       int(dist.get("low",       0) or 0),
            "medium":    int(dist.get("medium",    0) or 0),
            "high":      int(dist.get("high",      0) or 0),
            "very_high": int(dist.get("very_high", 0) or 0),
            "total":     int(dist.get("total",     0) or 0),
            "overall_avg": round(float(dist.get("overall_avg") or 0), 4),
        },
        "daily_avg": [
            {
                "day": r["day"],
                "avg_quality": round(float(r["avg_quality"] or 0), 4),
                "count": r["count"],
            }
            for r in daily_rows
        ],
        "route_quality": [
            {
                "route": r["route_used"],
                "avg_quality": round(float(r["avg_quality"] or 0), 4),
                "count": r["count"],
            }
            for r in route_rows
        ],
    }


@router.get("/dashboard/failures")
async def failure_dashboard(
    days: int = Query(7, ge=1, le=90),
    _: TokenData = Depends(require_admin),
):
    """Failure category breakdown from the healing queue."""
    rows = await fetch_all(
        settings.PROMPTS_DB,
        """SELECT
               section_name AS category,
               COUNT(*) AS count,
               SUM(CASE WHEN new_version != '' AND new_version IS NOT NULL THEN 1 ELSE 0 END) AS healed
           FROM prompt_evolution_log
           WHERE prompt_name = 'healing_queue'
             AND created_at >= datetime('now', ?)
           GROUP BY section_name
           ORDER BY count DESC""",
        (f"-{days} days",),
    )

    return {
        "period_days": days,
        "categories": [
            {
                "category": r["category"],
                "count": r["count"],
                "healed": r["healed"],
                "heal_rate": round(r["healed"] / r["count"], 2) if r["count"] > 0 else 0,
            }
            for r in rows
        ],
    }


@router.get("/dashboard/healing")
async def healing_queue_list(
    status: Optional[str] = Query(None, description="pending | approved | rejected | all"),
    limit: int = Query(20, ge=1, le=100),
    _: TokenData = Depends(require_admin),
):
    """Healing queue entries from feedback.db."""
    import json

    if status and status != "all":
        where = "WHERE status = ?"
        params: tuple = (status, limit)
    else:
        where = "WHERE status = 'pending'" if status is None else ""
        params = (limit,)

    rows = await fetch_all(
        settings.FEEDBACK_DB,
        f"""SELECT id, query, original_response, failure_type, fix_type,
                   fix_details, change_reason, risk_level, status, created_at
            FROM healing_queue
            {where}
            ORDER BY created_at DESC
            LIMIT ?""",
        params,
    )

    items = []
    for r in rows:
        try:
            details = json.loads(r.get("fix_details") or "{}")
        except (TypeError, ValueError):
            details = {}
        items.append({
            "id": r["id"],
            "query": r.get("query", ""),
            "failure_type": r.get("failure_type", ""),
            "fix_type": r.get("fix_type", ""),
            "fix_details": details,
            "change_reason": r.get("change_reason", ""),
            "risk_level": r.get("risk_level", "medium"),
            "status": r.get("status", "pending"),
            "created_at": r.get("created_at", ""),
        })

    return {"total": len(items), "status_filter": status or "pending", "queue": items}


@router.post("/dashboard/healing/{fix_id}/approve")
async def approve_healing(
    fix_id: str,
    _: TokenData = Depends(require_admin),
):
    """
    Approve a healing fix:
    1. Mark as approved in healing_queue
    2. Clear cached responses matching the failed query
    3. Log as training data so the system learns from the fix
    """
    import uuid
    from datetime import datetime

    now = datetime.utcnow().isoformat()

    # 1. Get the fix details before updating
    fix = await fetch_one(
        settings.FEEDBACK_DB,
        "SELECT query, original_response, failure_type, fix_type, change_reason FROM healing_queue WHERE id = ?",
        (fix_id,),
    )

    # 2. Mark as approved
    await execute(
        settings.FEEDBACK_DB,
        "UPDATE healing_queue SET status = 'approved', resolved_at = ?, resolved_by = 'admin' WHERE id = ?",
        (now, fix_id),
    )

    actions_taken = ["status updated to approved"]

    if fix:
        query_text = fix.get("query", "")

        # 3. Clear cache entries matching this query so it gets a fresh answer next time
        if query_text:
            try:
                import sqlite3
                conn = sqlite3.connect("data/cache.db")
                deleted = conn.execute(
                    "DELETE FROM query_cache WHERE query_text LIKE ?",
                    (f"%{query_text[:50]}%",),
                ).rowcount
                conn.commit()
                conn.close()
                if deleted > 0:
                    actions_taken.append(f"cleared {deleted} cached response(s)")
            except Exception as exc:
                logger.warning("Cache clear failed: %s", exc)

        # 4. Log as negative training example so future training avoids this pattern
        try:
            await execute(
                settings.FEEDBACK_DB,
                """INSERT OR IGNORE INTO training_candidates
                   (id, query, response, quality_score, category, source, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    query_text,
                    f"[HEALED] Original failed with: {fix.get('failure_type', 'unknown')}. Reason: {fix.get('change_reason', '')}",
                    0.1,  # low score = negative example
                    fix.get("fix_type", "manual_review"),
                    "healing_approved",
                    now,
                ),
            )
            actions_taken.append("logged as training data")
        except Exception as exc:
            logger.warning("Training data log failed: %s", exc)

    return {"message": f"Fix approved — {', '.join(actions_taken)}", "id": fix_id}


@router.post("/dashboard/healing/{fix_id}/reject")
async def reject_healing(
    fix_id: str,
    _: TokenData = Depends(require_admin),
):
    """Reject a healing fix."""
    from datetime import datetime
    await execute(
        settings.FEEDBACK_DB,
        "UPDATE healing_queue SET status = 'rejected', resolved_at = ?, resolved_by = 'admin' WHERE id = ?",
        (datetime.utcnow().isoformat(), fix_id),
    )
    return {"message": "Fix rejected", "id": fix_id}


@router.get("/dashboard/chunks")
async def chunk_analytics(
    limit: int = Query(20, ge=1, le=100),
    _: TokenData = Depends(require_admin),
):
    """Top retrieved chunks by usage count."""
    rows = await fetch_all(
        settings.FEEDBACK_DB,
        """SELECT chunk_id, times_retrieved
           FROM chunk_analytics
           ORDER BY times_retrieved DESC
           LIMIT ?""",
        (limit,),
    )
    return {
        "total_tracked_chunks": len(rows),
        "top_chunks": [
            {"chunk_id": r["chunk_id"], "times_retrieved": r["times_retrieved"]}
            for r in rows
        ],
    }


@router.post("/dashboard/refresh-schema")
async def refresh_schema(_: TokenData = Depends(require_admin)):
    """Clear and re-read the database schema cache."""
    from core.schema_reader import schema_reader

    schema_reader.clear_cache()
    schema = await schema_reader.read_schema()
    return {
        "message": "Schema refreshed",
        "tables": len(schema["tables"]),
        "foreign_keys": len(schema["foreign_keys"]),
        "row_counts": schema["row_counts"],
    }

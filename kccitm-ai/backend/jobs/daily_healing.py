"""
Daily batch job: heal failed queries and collect training data.

Steps:
1. Score any unscored feedback rows
2. Find low-quality unhealed entries from the past 24 h
3. Classify and add to healing queue
4. Auto-collect high-quality feedback into the training pool
5. Auto-collect verified FAQs into the training pool
"""

import logging
from datetime import datetime

from adaptive.failure_classifier import add_to_healing_queue, classify_failure
from adaptive.quality_scorer import score_feedback_row
from adaptive.training_data_manager import TrainingDataManager
from config import settings
from db.sqlite_client import fetch_all

logger = logging.getLogger(__name__)


async def run() -> dict:
    start = datetime.utcnow().isoformat()
    print(f"\n[{start}] Starting daily healing job...")

    # 1. Score unscored feedback
    unscored = await fetch_all(
        settings.FEEDBACK_DB,
        "SELECT id FROM feedback WHERE quality_score IS NULL LIMIT 500",
    )
    scored = 0
    for row in (unscored or []):
        try:
            await score_feedback_row(row["id"])
            scored += 1
        except Exception as exc:
            logger.warning("Could not score feedback %s: %s", row["id"], exc)
    print(f"  Scored {scored} unscored feedback entries")

    # 2. Find severe unhealed failures from the past day
    failures = await fetch_all(
        settings.FEEDBACK_DB,
        """SELECT id, query_text, response_text, quality_score, route_used,
                  chunks_used
           FROM feedback
           WHERE quality_score < 0.3 AND healed = 0
             AND created_at > datetime('now', '-1 day')""",
    )

    healed_count = 0
    for row in (failures or []):
        try:
            import json
            chunks = json.loads(row.get("chunks_used") or "[]") or []
            category = classify_failure(
                query=row.get("query_text") or "",
                response_text=row.get("response_text") or "",
                route_used=row.get("route_used") or "",
                sql_row_count=None,
                sql_error=None,
                chunk_count=len(chunks),
                quality_score=row.get("quality_score") or 0,
            )
            if category:
                await add_to_healing_queue(
                    feedback_id=row["id"],
                    query=row.get("query_text") or "",
                    response=row.get("response_text") or "",
                    failure_category=category,
                    quality_score=row.get("quality_score") or 0,
                )
                healed_count += 1
        except Exception as exc:
            logger.warning("Healing classification failed for %s: %s", row["id"], exc)

    print(f"  Added {healed_count} entries to healing queue")

    # 3. Collect training data
    training = TrainingDataManager()
    from_feedback = await training.auto_collect_from_feedback()
    from_faqs     = await training.auto_collect_from_faqs()
    stats = await training.get_stats()
    print(
        f"  Training data: +{from_feedback} from feedback, +{from_faqs} from FAQs "
        f"(total: {stats['total_candidates']})"
    )
    print(f"  Daily healing complete.")

    return {
        "scored": scored,
        "queued_for_healing": healed_count,
        "training_from_feedback": from_feedback,
        "training_from_faqs": from_faqs,
        "training_total": stats["total_candidates"],
    }

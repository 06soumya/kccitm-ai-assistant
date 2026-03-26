"""
Training data manager — accumulates high-quality Q&A pairs for LoRA fine-tuning (Phase 10).

Auto-collection rules:
- quality_score > 0.8 → auto-add from feedback
- admin_verified FAQ entries → auto-add
- Healed corrections → add corrected version

Categories: routing | sql_gen | response (for targeted LoRA training)
"""

import logging
import uuid
from datetime import datetime

from config import settings
from db.sqlite_client import execute, fetch_all, fetch_one

logger = logging.getLogger(__name__)


class TrainingDataManager:

    # ── Collection ────────────────────────────────────────────────────────────

    async def auto_collect_from_feedback(self) -> int:
        """Scan high-quality feedback and add new entries to the training pool."""
        good = await fetch_all(
            settings.FEEDBACK_DB,
            """SELECT id, query_text, response_text, quality_score, route_used
               FROM feedback
               WHERE quality_score >= 0.8
               LIMIT 100""",
        )
        count = 0
        for entry in (good or []):
            if not entry.get("query_text"):
                continue
            existing = await fetch_one(
                settings.FEEDBACK_DB,
                "SELECT id FROM training_candidates WHERE query = ? AND source = 'feedback_positive'",
                (entry["query_text"],),
            )
            if existing:
                continue
            category = self._categorize(entry.get("route_used", ""))
            await self._add_candidate(
                query=entry["query_text"],
                response=entry["response_text"] or "",
                quality_score=entry["quality_score"],
                category=category,
                source="feedback_positive",
            )
            count += 1
        return count

    async def auto_collect_from_faqs(self) -> int:
        """Add admin-verified FAQs to the training pool."""
        faqs = await fetch_all(
            settings.PROMPTS_DB,
            """SELECT id, canonical_question, answer FROM faq_entries
               WHERE admin_verified = 1 AND status = 'active'""",
        )
        count = 0
        for faq in (faqs or []):
            existing = await fetch_one(
                settings.FEEDBACK_DB,
                "SELECT id FROM training_candidates WHERE query = ? AND source = 'faq'",
                (faq["canonical_question"],),
            )
            if not existing:
                await self._add_candidate(
                    query=faq["canonical_question"],
                    response=faq["answer"],
                    quality_score=1.0,
                    category="response",
                    source="faq",
                )
                count += 1
        return count

    # ── Retrieval ─────────────────────────────────────────────────────────────

    async def get_stats(self) -> dict:
        total = await fetch_one(
            settings.FEEDBACK_DB,
            "SELECT COUNT(*) AS cnt FROM training_candidates",
        )
        rows = await fetch_all(
            settings.FEEDBACK_DB,
            "SELECT category, source, COUNT(*) AS cnt FROM training_candidates GROUP BY category, source",
        )
        total_count = total["cnt"] if total else 0
        return {
            "total_candidates": total_count,
            "by_category": [dict(r) for r in (rows or [])],
            "ready_for_lora": total_count >= 500,
        }

    async def get_candidates(
        self,
        category: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        if category:
            rows = await fetch_all(
                settings.FEEDBACK_DB,
                "SELECT * FROM training_candidates WHERE category = ? ORDER BY quality_score DESC LIMIT ?",
                (category, limit),
            )
        else:
            rows = await fetch_all(
                settings.FEEDBACK_DB,
                "SELECT * FROM training_candidates ORDER BY quality_score DESC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in (rows or [])]

    async def exclude_candidate(self, candidate_id: str) -> None:
        await execute(
            settings.FEEDBACK_DB,
            "DELETE FROM training_candidates WHERE id = ?",
            (candidate_id,),
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _add_candidate(
        self,
        query: str,
        response: str,
        quality_score: float,
        category: str,
        source: str,
    ) -> None:
        await execute(
            settings.FEEDBACK_DB,
            """INSERT INTO training_candidates
               (id, query, response, quality_score, category, source, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()), query, response, quality_score,
                category, source, datetime.utcnow().isoformat(),
            ),
        )

    @staticmethod
    def _categorize(route_used: str) -> str:
        if "SQL" in (route_used or ""):
            return "sql_gen"
        return "response"

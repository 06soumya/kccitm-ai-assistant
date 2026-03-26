"""
Chunk performance tracker — monitors retrieval quality per chunk over time.
Identifies underperformers for re-chunking proposals.
"""

import logging

from config import settings
from db.sqlite_client import execute, fetch_all, fetch_one

logger = logging.getLogger(__name__)


class ChunkAnalyzer:

    async def record_retrieval(
        self,
        chunk_id: str,
        rerank_score: float | None = None,
        in_top5: bool = False,
        in_final: bool = False,
    ) -> None:
        """Record one retrieval event for a chunk (upsert)."""
        existing = await fetch_one(
            settings.FEEDBACK_DB,
            "SELECT * FROM chunk_analytics WHERE chunk_id = ?",
            (chunk_id,),
        )

        if existing:
            new_retrieved = (existing["times_retrieved"] or 0) + 1
            new_top5     = (existing["times_reranked_top5"] or 0) + (1 if in_top5 else 0)
            new_final    = (existing["times_in_final_context"] or 0) + (1 if in_final else 0)
            old_avg   = existing["avg_reranker_score"] or 0
            old_count = existing["times_retrieved"] or 1
            new_avg   = (old_avg * old_count + rerank_score) / (old_count + 1) if rerank_score is not None else old_avg

            await execute(
                settings.FEEDBACK_DB,
                """UPDATE chunk_analytics SET
                       times_retrieved = ?, times_reranked_top5 = ?,
                       times_in_final_context = ?, avg_reranker_score = ?,
                       last_retrieved_at = datetime('now'), never_retrieved = 0
                   WHERE chunk_id = ?""",
                (new_retrieved, new_top5, new_final, new_avg, chunk_id),
            )
        else:
            await execute(
                settings.FEEDBACK_DB,
                """INSERT INTO chunk_analytics
                       (chunk_id, times_retrieved, times_reranked_top5,
                        times_in_final_context, avg_reranker_score,
                        last_retrieved_at, never_retrieved)
                   VALUES (?, 1, ?, ?, ?, datetime('now'), 0)""",
                (chunk_id, 1 if in_top5 else 0, 1 if in_final else 0, rerank_score or 0),
            )

    async def get_underperforming_chunks(
        self,
        min_retrievals: int = 10,
        max_ratio: float = 0.2,
    ) -> list[dict]:
        """
        Chunks retrieved often but rarely making top-5 after reranking.
        These have misleading dense embeddings.
        """
        rows = await fetch_all(
            settings.FEEDBACK_DB,
            """SELECT chunk_id, times_retrieved, times_reranked_top5,
                      avg_reranker_score,
                      CAST(times_reranked_top5 AS REAL) / NULLIF(times_retrieved, 0) AS ratio
               FROM chunk_analytics
               WHERE times_retrieved >= ?
                 AND CAST(times_reranked_top5 AS REAL) / NULLIF(times_retrieved, 0) < ?
               ORDER BY ratio ASC""",
            (min_retrievals, max_ratio),
        )
        return [dict(r) for r in (rows or [])]

    async def get_never_retrieved(self) -> list[dict]:
        rows = await fetch_all(
            settings.FEEDBACK_DB,
            "SELECT chunk_id FROM chunk_analytics WHERE never_retrieved = 1",
        )
        return [dict(r) for r in (rows or [])]

    async def get_health_summary(self) -> dict:
        total = await fetch_one(
            settings.FEEDBACK_DB,
            "SELECT COUNT(*) AS cnt FROM chunk_analytics",
        )
        underperforming = await self.get_underperforming_chunks()
        never = await self.get_never_retrieved()
        top_chunks = await fetch_all(
            settings.FEEDBACK_DB,
            """SELECT chunk_id, times_retrieved, times_reranked_top5, avg_reranker_score
               FROM chunk_analytics ORDER BY times_retrieved DESC LIMIT 10""",
        )
        return {
            "total_tracked": total["cnt"] if total else 0,
            "underperforming_count": len(underperforming),
            "never_retrieved_count": len(never),
            "top_chunks": [dict(r) for r in (top_chunks or [])],
        }

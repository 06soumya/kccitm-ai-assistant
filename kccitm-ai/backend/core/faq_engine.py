"""
FAQ lookup engine — checked early in the pipeline, after cache, before routing.
If a high-confidence FAQ match is found, returns the pre-computed answer instantly
without running the full RAG/SQL pipeline.
"""

import logging

from config import settings
from core.llm_client import OllamaClient
from db.milvus_client import MilvusSearchClient
from db.sqlite_client import execute

logger = logging.getLogger(__name__)


class FAQEngine:
    """
    Searches the Milvus FAQ collection for matching entries.
    High-confidence matches (≥ confidence_threshold) are returned immediately.
    """

    def __init__(self, llm: OllamaClient, milvus: MilvusSearchClient) -> None:
        self.llm = llm
        self.milvus = milvus
        self.confidence_threshold = 0.85

    async def check(self, query: str) -> dict | None:
        """
        Check if the query matches a FAQ entry.

        Returns:
            {"question": "...", "answer": "...", "faq_id": "...", "confidence": 0.95}
            or None if no match above threshold.
        """
        try:
            embedding = await self.llm.embed(query)
            result = self.milvus.search_faq(
                query_text=query,
                query_embedding=embedding,
                k=1,
            )
            if result and result.get("score", 0) >= self.confidence_threshold:
                faq_id = result.get("faq_id") or result.get("chunk_id", "")
                if faq_id:
                    await execute(
                        settings.PROMPTS_DB,
                        "UPDATE faq_entries SET hit_count = hit_count + 1, last_hit_at = datetime('now') WHERE id = ?",
                        (faq_id,),
                    )
                return {
                    "question":   result.get("question", ""),
                    "answer":     result.get("answer", ""),
                    "faq_id":     faq_id,
                    "confidence": result.get("score", 0),
                }
        except Exception as exc:
            logger.debug("FAQ check failed (non-critical): %s", exc)
        return None

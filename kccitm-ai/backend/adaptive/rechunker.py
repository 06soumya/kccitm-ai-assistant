"""
Rechunker — proposes re-chunking strategies for underperforming chunks.
Admin reviews and triggers actual re-chunking manually.

Strategies
----------
rephrase   — add query-aligned vocabulary to chunk text
split      — break large chunks into focused sub-chunks
merge      — combine with adjacent related chunks
enrich     — add contextual data (branch averages, session annotations)
"""

import logging

from adaptive.chunk_analyzer import ChunkAnalyzer
from core.llm_client import OllamaClient
from db.milvus_client import MilvusSearchClient

logger = logging.getLogger(__name__)


class Rechunker:

    def __init__(self, llm: OllamaClient, milvus: MilvusSearchClient) -> None:
        self.llm = llm
        self.milvus = milvus
        self.analyzer = ChunkAnalyzer()

    async def generate_proposals(self) -> list[dict]:
        """
        Analyze underperforming chunks and generate improvement proposals.
        Returns up to 10 proposals per run.
        """
        underperformers = await self.analyzer.get_underperforming_chunks()
        proposals: list[dict] = []

        for chunk_info in underperformers[:10]:
            # Fetch chunk text from Milvus
            try:
                results = self.milvus.client.get(
                    collection_name="student_results",
                    ids=[chunk_info["chunk_id"]],
                    output_fields=["text", "roll_no", "name", "branch", "semester"],
                )
                if not results:
                    continue
                chunk_data = results[0]
            except Exception as exc:
                logger.debug("Could not fetch chunk %s: %s", chunk_info["chunk_id"], exc)
                continue

            ratio = chunk_info.get("ratio") or 0
            strategy = "rephrase"
            if ratio == 0:
                strategy = "split"
            elif (chunk_info.get("avg_reranker_score") or 0) < 0.2:
                strategy = "enrich"

            proposals.append({
                "chunk_id": chunk_info["chunk_id"],
                "current_text": (chunk_data.get("text") or "")[:300],
                "times_retrieved": chunk_info["times_retrieved"],
                "rerank_ratio": round(ratio, 3),
                "avg_reranker_score": round(chunk_info.get("avg_reranker_score") or 0, 3),
                "strategy": strategy,
                "reason": (
                    f"Retrieved {chunk_info['times_retrieved']} times but only made top-5 "
                    f"{chunk_info.get('times_reranked_top5', 0)} times"
                ),
            })

        return proposals

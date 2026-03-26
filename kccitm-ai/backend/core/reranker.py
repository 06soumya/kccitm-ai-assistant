"""
Cross-encoder re-ranking for KCCITM AI Assistant.

Scores query-chunk pairs jointly (not independently like bi-encoders),
giving more accurate relevance judgments. Runs after retrieval to
narrow 30 candidates down to the top 10 with high precision.

Model: cross-encoder/ms-marco-MiniLM-L-6-v2 (~80MB, CPU-only, ~50-80ms/30 pairs)

Usage:
    reranker = ChunkReranker()
    reranked = reranker.rerank("programming performance", chunks, top_k=10)
"""

import logging

from config import settings

logger = logging.getLogger(__name__)


class ChunkReranker:
    """
    Re-ranks retrieved chunks using a cross-encoder model.

    Unlike bi-encoders (which encode query and document separately),
    cross-encoders process the query-document pair TOGETHER, giving
    much more accurate relevance scores at the cost of being slower.

    Pipeline position: after retrieval (30 candidates) → re-rank → top 10.
    Latency: ~50-80ms for 30 pairs on CPU.

    The model is loaded lazily and cached in-process (singleton pattern).
    """

    # Class-level model cache — loaded once, shared across all instances
    _model_cache: dict = {}

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or settings.RERANKER_MODEL

    @property
    def model(self):
        """Lazy-load cross-encoder model; cached at class level after first load."""
        if self.model_name not in ChunkReranker._model_cache:
            from sentence_transformers import CrossEncoder
            logger.info("Loading re-ranker model: %s", self.model_name)
            ChunkReranker._model_cache[self.model_name] = CrossEncoder(self.model_name)
            logger.info("Re-ranker loaded.")
        return ChunkReranker._model_cache[self.model_name]

    def rerank(
        self,
        query: str,
        chunks: list[dict],
        top_k: int | None = None,
        text_field: str = "text",
    ) -> list[dict]:
        """
        Re-rank chunks by cross-encoder relevance score.

        Args:
            query: User's query
            chunks: List of chunk dicts (must have a text field)
            top_k: Number of top results to return (default: settings.RAG_RERANK_TOP_K)
            text_field: Key in chunk dict that contains the text

        Returns:
            Top-k chunks sorted by cross-encoder score descending.
            Each chunk gets a 'rerank_score' field added.
        """
        top_k = top_k or settings.RAG_RERANK_TOP_K

        if not chunks:
            return []

        texts = [chunk.get(text_field, "") for chunk in chunks]
        pairs = [[query, text] for text in texts]

        try:
            scores = self.model.predict(pairs)
        except Exception as exc:
            logger.warning("Re-ranker predict failed: %s — returning original order", exc)
            return chunks[:top_k]

        scored: list[dict] = []
        for chunk, score in zip(chunks, scores):
            chunk_copy = chunk.copy()
            chunk_copy["rerank_score"] = float(score)
            scored.append(chunk_copy)

        scored.sort(key=lambda x: x["rerank_score"], reverse=True)
        return scored[:top_k]

    def get_score_stats(self, chunks: list[dict]) -> dict:
        """
        Return min/max/mean re-rank scores for monitoring.
        Used by the adaptive chunk analyzer in Phase 9.
        """
        if not chunks:
            return {"min": 0.0, "max": 0.0, "mean": 0.0, "count": 0}
        scores = [c.get("rerank_score", 0.0) for c in chunks]
        return {
            "min": min(scores),
            "max": max(scores),
            "mean": sum(scores) / len(scores),
            "count": len(scores),
        }

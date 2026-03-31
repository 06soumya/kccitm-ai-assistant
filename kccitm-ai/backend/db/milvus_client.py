"""
Unified Milvus search client for KCCITM AI Assistant.

Wraps Milvus hybrid_search (dense + BM25 + RRF) into clean methods.
This single client replaces what would have been three separate systems
(FAISS, ChromaDB, BM25).

Usage:
    from db.milvus_client import MilvusSearchClient
    from config import settings

    client = MilvusSearchClient(settings.MILVUS_HOST, settings.MILVUS_PORT)
    results = client.hybrid_search(
        query_text="top CSE students semester 4",
        query_embedding=[0.1, 0.2, ...],   # 768-dim from nomic-embed-text
        k=30,
        filters={"semester": 4}
    )
"""

import logging
from typing import Any

from pymilvus import MilvusClient, AnnSearchRequest, RRFRanker

from config import settings as _settings

logger = logging.getLogger(__name__)

# Output fields returned on every search
_OUTPUT_FIELDS = [
    "text", "chunk_id", "roll_no", "name", "branch",
    "course", "semester", "sgpa", "session", "result_status", "gender",
]


class MilvusSearchClient:
    """
    Unified search client for Milvus collections.

    Supports:
      - hybrid_search(): dense (HNSW/COSINE) + sparse (BM25) + RRF fusion
      - dense_search(): semantic-only (for HyDE or when BM25 not needed)
      - keyword_search(): BM25-only (for exact subject codes / roll numbers)
      - search_faq(): search the FAQ collection
      - get_collection_stats(): entity count and index status
    """

    def __init__(
        self,
        uri: str = None,
        collection: str = None,
    ) -> None:
        """
        Initialise the client.

        Args:
            uri: Milvus URI (defaults to settings.milvus_uri — file path or HTTP)
            collection: Collection name (defaults to settings.MILVUS_COLLECTION)
        """
        self.uri = uri or _settings.milvus_uri
        self.collection = collection or _settings.MILVUS_COLLECTION
        self.client = MilvusClient(uri=self.uri)
        logger.info("MilvusSearchClient connected to %s", self.uri)

    # ── Primary search methods ────────────────────────────────────────────────

    def hybrid_search(
        self,
        query_text: str,
        query_embedding: list[float],
        k: int = 30,
        filters: dict | None = None,
    ) -> list[dict]:
        """
        Perform hybrid search: dense (semantic) + sparse (BM25 keyword) + RRF fusion.

        Args:
            query_text: Raw text query (tokenized by Milvus for BM25)
            query_embedding: 768-dim dense embedding from nomic-embed-text
            k: Number of results to return
            filters: Optional metadata filters, e.g. {"semester": 4, "branch": "CSE"}

        Returns:
            List of result dicts sorted by RRF score (descending).
        """
        dense_req = AnnSearchRequest(
            data=[query_embedding],
            anns_field="dense",
            param={"metric_type": "COSINE", "params": {"ef": 64}},
            limit=k,
        )

        sparse_req = AnnSearchRequest(
            data=[query_text],
            anns_field="sparse",
            param={"metric_type": "BM25"},
            limit=k,
        )

        filter_expr = self._build_filter(filters)

        results = self.client.hybrid_search(
            collection_name=self.collection,
            reqs=[dense_req, sparse_req],
            ranker=RRFRanker(k=60),
            filter=filter_expr,
            output_fields=_OUTPUT_FIELDS,
            limit=k,
        )

        return self._format_results(results)

    def dense_search(
        self,
        query_embedding: list[float],
        k: int = 30,
        filters: dict | None = None,
    ) -> list[dict]:
        """
        Dense-only ANN search using HNSW/COSINE index.

        Useful for HyDE (hypothetical document embeddings) or when
        keyword matching is not needed.
        """
        filter_expr = self._build_filter(filters)
        results = self.client.search(
            collection_name=self.collection,
            data=[query_embedding],
            anns_field="dense",
            search_params={"metric_type": "COSINE", "params": {"ef": 64}},
            filter=filter_expr,
            output_fields=_OUTPUT_FIELDS,
            limit=k,
        )
        return self._format_results(results)

    def keyword_search(
        self,
        query_text: str,
        k: int = 30,
        filters: dict | None = None,
    ) -> list[dict]:
        """
        BM25-only full-text search.

        Best for exact subject codes (e.g. "KCS503"), roll numbers,
        or when semantic matching is not needed.
        """
        filter_expr = self._build_filter(filters)
        results = self.client.search(
            collection_name=self.collection,
            data=[query_text],
            anns_field="sparse",
            search_params={"metric_type": "BM25"},
            filter=filter_expr,
            output_fields=_OUTPUT_FIELDS,
            limit=k,
        )
        return self._format_results(results)

    def search_faq(
        self,
        query_text: str,
        query_embedding: list[float],
        k: int = 1,
    ) -> dict | None:
        """
        Search the FAQ collection with hybrid search.

        Returns the best matching FAQ entry as a dict, or None if
        collection is empty.
        """
        faq_collection = _settings.MILVUS_FAQ_COLLECTION

        stats = self.client.get_collection_stats(faq_collection)
        if int(stats.get("row_count", 0)) == 0:
            return None

        dense_req = AnnSearchRequest(
            data=[query_embedding],
            anns_field="dense",
            param={"metric_type": "COSINE", "params": {"ef": 32}},
            limit=k,
        )
        sparse_req = AnnSearchRequest(
            data=[query_text],
            anns_field="sparse",
            param={"metric_type": "BM25"},
            limit=k,
        )

        results = self.client.hybrid_search(
            collection_name=faq_collection,
            reqs=[dense_req, sparse_req],
            ranker=RRFRanker(k=60),
            output_fields=["faq_id", "question", "answer"],
            limit=k,
        )

        formatted = self._format_results(results)
        return formatted[0] if formatted else None

    def get_collection_stats(self, collection_name: str | None = None) -> dict:
        """
        Return collection statistics.

        Returns:
            {"collection": name, "row_count": N, "status": "loaded"|"not_loaded"}
        """
        name = collection_name or self.collection
        try:
            stats = self.client.get_collection_stats(name)
            return {
                "collection": name,
                "row_count": int(stats.get("row_count", 0)),
                "status": "loaded",
            }
        except Exception as exc:
            return {"collection": name, "row_count": 0, "status": str(exc)}

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _build_filter(self, filters: dict | None) -> str | None:
        """Convert a filter dict to a Milvus boolean filter expression."""
        if not filters:
            return None

        conditions: list[str] = []

        if "semester" in filters:
            conditions.append(f'semester == {int(filters["semester"])}')
        if "branch" in filters:
            safe = str(filters["branch"]).replace('"', '\\"')
            conditions.append(f'branch == "{safe}"')
        if "roll_no" in filters:
            safe = str(filters["roll_no"]).replace('"', '\\"')
            conditions.append(f'roll_no == "{safe}"')
        if "name" in filters:
            safe = str(filters["name"]).replace('"', '\\"').replace("%", "")
            conditions.append(f'name like "%{safe}%"')
        if "course" in filters:
            safe = str(filters["course"]).replace('"', '\\"')
            conditions.append(f'course == "{safe}"')

        return " and ".join(conditions) if conditions else None

    def _format_results(self, raw_results: Any) -> list[dict]:
        """Convert raw Milvus search results to clean Python dicts."""
        formatted: list[dict] = []
        if not raw_results:
            return formatted

        hits = raw_results[0] if isinstance(raw_results, list) else raw_results

        for hit in hits:
            entity = hit.get("entity", {}) if isinstance(hit, dict) else {}
            chunk_id = hit.get("id", "") if isinstance(hit, dict) else getattr(hit, "id", "")
            score = hit.get("distance", 0.0) if isinstance(hit, dict) else getattr(hit, "distance", 0.0)

            if not entity:
                # Attribute-style access (older pymilvus)
                try:
                    entity = {f: hit.entity.get(f) for f in _OUTPUT_FIELDS}
                    chunk_id = hit.id
                    score = hit.distance
                except Exception:
                    pass

            formatted.append({
                "chunk_id": chunk_id,
                "text": entity.get("text", ""),
                "score": score,
                "metadata": {
                    "roll_no": entity.get("roll_no", ""),
                    "name": entity.get("name", ""),
                    "branch": entity.get("branch", ""),
                    "course": entity.get("course", ""),
                    "semester": entity.get("semester"),
                    "sgpa": entity.get("sgpa"),
                    "session": entity.get("session", ""),
                    "result_status": entity.get("result_status", ""),
                    "gender": entity.get("gender", ""),
                },
            })

        return formatted

    # ── AKTU Knowledge search (separate collection) ──────────────────────────

    def search_aktu(
        self, query_text: str, query_embedding: list[float], k: int = 10,
    ) -> list[dict]:
        """
        Search the aktu_knowledge collection using hybrid (dense + BM25).
        Returns results with text, score, source_file, content_type.

        Does NOT touch the student_results collection.
        """
        aktu_collection = _settings.MILVUS_AKTU_COLLECTION

        try:
            stats = self.client.get_collection_stats(aktu_collection)
            if int(stats.get("row_count", 0)) == 0:
                return []
        except Exception:
            return []

        output_fields = ["text", "source_file", "content_type", "chunk_index"]

        try:
            from pymilvus import AnnSearchRequest, RRFRanker

            dense_req = AnnSearchRequest(
                data=[query_embedding],
                anns_field="dense",
                param={"metric_type": "COSINE", "params": {"nprobe": 16}},
                limit=k,
            )
            sparse_req = AnnSearchRequest(
                data=[query_text],
                anns_field="sparse",
                param={"metric_type": "BM25"},
                limit=k,
            )

            results = self.client.hybrid_search(
                collection_name=aktu_collection,
                reqs=[dense_req, sparse_req],
                ranker=RRFRanker(k=60),
                limit=k,
                output_fields=output_fields,
            )

            formatted = []
            hits = results[0] if isinstance(results, list) and results else results
            for hit in hits:
                entity = hit.get("entity", {}) if isinstance(hit, dict) else {}
                score = hit.get("distance", 0.0) if isinstance(hit, dict) else getattr(hit, "distance", 0.0)
                if not entity:
                    try:
                        entity = {f: hit.entity.get(f) for f in output_fields}
                        score = hit.distance
                    except Exception:
                        pass
                formatted.append({
                    "text": entity.get("text", ""),
                    "score": score,
                    "metadata": {
                        "source_file": entity.get("source_file", ""),
                        "content_type": entity.get("content_type", ""),
                        "chunk_index": entity.get("chunk_index", 0),
                    },
                })
            return formatted

        except Exception as exc:
            logger.warning("AKTU search failed: %s", exc)
            return []

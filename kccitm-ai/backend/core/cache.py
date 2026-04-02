"""
Two-tier semantic query cache for KCCITM AI Assistant.

Tier 1: Exact SHA256 hash match on normalized query — instant, 100% confidence.
Tier 2: Embedding cosine similarity — catches paraphrased / reworded queries.

Cache entries have a configurable TTL (default 24 hours) and are invalidated
on ETL re-run via cache.clear().

Usage:
    cache = QueryCache(llm)
    hit = await cache.check("top 5 students by SGPA")
    if hit:
        return hit.response  # instant
    # ... run pipeline ...
    await cache.store("top 5 students by SGPA", response, "SQL")
"""

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from config import settings
from core.llm_client import OllamaClient
from db.sqlite_client import execute, fetch_all, fetch_one

logger = logging.getLogger(__name__)


@dataclass
class CacheHit:
    """A cache hit result."""
    query_text: str
    response: str
    route_used: str
    metadata: dict
    confidence: float    # 1.0 for exact match, cosine similarity for semantic
    cache_type: str      # "exact" or "semantic"


class QueryCache:
    """
    Two-tier semantic query cache.

    Tier 1: Exact hash match (SHA256 of normalized query) — instant, 100% confidence.
    Tier 2: Semantic similarity via embedding cosine distance — catches paraphrased queries.

    In-memory numpy index for Tier 2 — fast O(n) cosine scan, no external vector DB needed.
    For ~1000 entries the scan takes < 1ms.  Index is lazily rebuilt from SQLite.
    """

    def __init__(self, llm: OllamaClient, db_path: str = None):
        self.llm = llm
        self.db_path = db_path or settings.CACHE_DB
        self.similarity_threshold: float = settings.CACHE_SIMILARITY_THRESHOLD  # 0.92
        self.ttl_hours: int = settings.CACHE_TTL_HOURS                           # 24

        # In-memory embedding index (rebuilt lazily)
        self._embeddings_cache: np.ndarray | None = None
        self._ids_cache: list[str] = []
        self._loaded: bool = False

    # ── Public API ────────────────────────────────────────────────────────────

    async def check(self, query: str) -> CacheHit | None:
        """
        Check if a query has a cached response.

        Tier 1: Exact hash match (instant).
        Tier 2: Semantic similarity match (embed + cosine).

        Returns CacheHit if found, None on miss.
        """
        # Tier 1 — exact hash
        normalized = self._normalize_query(query)
        query_hash = hashlib.sha256(normalized.encode()).hexdigest()

        row = await fetch_one(
            self.db_path,
            "SELECT * FROM query_cache WHERE query_hash = ? AND created_at > ?",
            (query_hash, self._ttl_cutoff()),
        )

        if row:
            await execute(
                self.db_path,
                "UPDATE query_cache SET hit_count = hit_count + 1, last_hit_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), row["id"]),
            )
            return CacheHit(
                query_text=row["query_text"],
                response=row["response"],
                route_used=row.get("route_used", ""),
                metadata=json.loads(row["metadata"]) if row.get("metadata") else {},
                confidence=1.0,
                cache_type="exact",
            )

        # Tier 2 — semantic similarity
        return await self._semantic_match(query)

    _FAILURE_PHRASES = (
        "don't have sufficient",
        "do not have sufficient",
        "don't have enough",
        "no data available",
        "unable to retrieve",
        "could not find",
        "i cannot answer",
        "insufficient information",
        "no results found",
        "i don't have information",
    )

    async def store(
        self,
        query: str,
        response: str,
        route_used: str = "",
        metadata: dict = None,
    ) -> None:
        """
        Store a query-response pair in the cache with its embedding.

        If the exact same normalized query already exists, the entry is refreshed
        (response + created_at updated) rather than duplicated.

        Skips caching failure/error responses to prevent serving bad answers.
        """
        # Never cache failure responses
        response_lower = response.lower()
        if any(phrase in response_lower for phrase in self._FAILURE_PHRASES):
            logger.debug("Cache: skipping store — response looks like a failure: %s", response[:80])
            return
        normalized = self._normalize_query(query)
        query_hash = hashlib.sha256(normalized.encode()).hexdigest()

        existing = await fetch_one(
            self.db_path,
            "SELECT id FROM query_cache WHERE query_hash = ?",
            (query_hash,),
        )

        if existing:
            await execute(
                self.db_path,
                "UPDATE query_cache SET response = ?, route_used = ?, metadata = ?, created_at = ? WHERE id = ?",
                (response, route_used, json.dumps(metadata or {}),
                 datetime.utcnow().isoformat(), existing["id"]),
            )
            self._loaded = False  # invalidate in-memory index
            return

        # Generate embedding for semantic Tier 2
        try:
            embedding = await self.llm.embed(query)
            embedding_blob = np.array(embedding, dtype=np.float32).tobytes()
        except Exception as e:
            logger.warning("Cache: failed to embed query for semantic index: %s", e)
            embedding_blob = None

        cache_id = str(uuid.uuid4())
        await execute(
            self.db_path,
            """INSERT INTO query_cache
               (id, query_text, query_hash, query_embedding, response, route_used, metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (cache_id, query, query_hash, embedding_blob, response,
             route_used, json.dumps(metadata or {}), datetime.utcnow().isoformat()),
        )

        self._loaded = False  # invalidate in-memory index

    async def clear(self) -> None:
        """Clear all cache entries. Called on ETL re-run or by admin."""
        await execute(self.db_path, "DELETE FROM query_cache")
        self._loaded = False
        self._embeddings_cache = None
        self._ids_cache = []

    async def clear_expired(self) -> None:
        """Remove entries older than TTL."""
        await execute(
            self.db_path,
            "DELETE FROM query_cache WHERE created_at < ?",
            (self._ttl_cutoff(),),
        )
        self._loaded = False

    async def get_stats(self) -> dict:
        """Cache statistics for the admin dashboard."""
        total = await fetch_one(self.db_path, "SELECT COUNT(*) AS cnt FROM query_cache")
        active = await fetch_one(
            self.db_path,
            "SELECT COUNT(*) AS cnt FROM query_cache WHERE created_at > ?",
            (self._ttl_cutoff(),),
        )
        hits = await fetch_one(
            self.db_path,
            "SELECT SUM(hit_count) AS total_hits FROM query_cache",
        )
        top_queries = await fetch_all(
            self.db_path,
            "SELECT query_text, hit_count, route_used FROM query_cache ORDER BY hit_count DESC LIMIT 10",
        )

        return {
            "total_entries": total["cnt"] if total else 0,
            "active_entries": active["cnt"] if active else 0,
            "total_hits": hits["total_hits"] if hits and hits["total_hits"] else 0,
            "top_queries": [dict(r) for r in (top_queries or [])],
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _semantic_match(self, query: str) -> CacheHit | None:
        """Find a semantically similar cached query via embedding cosine distance."""
        if not self._loaded:
            await self._load_embedding_index()

        if self._embeddings_cache is None or len(self._embeddings_cache) == 0:
            return None

        try:
            query_embedding = np.array(await self.llm.embed(query), dtype=np.float32)
        except Exception as e:
            logger.warning("Cache: failed to embed query for semantic search: %s", e)
            return None

        # Cosine similarity: normalise both sides, then dot product
        query_norm = query_embedding / (np.linalg.norm(query_embedding) + 1e-8)
        cache_norms = self._embeddings_cache / (
            np.linalg.norm(self._embeddings_cache, axis=1, keepdims=True) + 1e-8
        )
        similarities = cache_norms @ query_norm

        best_idx = int(np.argmax(similarities))
        best_sim = float(similarities[best_idx])

        if best_sim < self.similarity_threshold:
            return None

        cache_id = self._ids_cache[best_idx]
        row = await fetch_one(
            self.db_path,
            "SELECT * FROM query_cache WHERE id = ? AND created_at > ?",
            (cache_id, self._ttl_cutoff()),
        )

        if not row:
            return None

        # ADDITIVE GUARD: reject semantic hit if key entities differ
        # (e.g. "semester 1" vs "semester 3", "batch 2021" vs "batch 2022")
        if not self._entities_match(query, row["query_text"]):
            logger.debug(
                "Cache: semantic match rejected — entities differ: '%s' vs '%s'",
                query[:50], row["query_text"][:50],
            )
            return None

        await execute(
            self.db_path,
            "UPDATE query_cache SET hit_count = hit_count + 1, last_hit_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), row["id"]),
        )

        return CacheHit(
            query_text=row["query_text"],
            response=row["response"],
            route_used=row.get("route_used", ""),
            metadata=json.loads(row["metadata"]) if row.get("metadata") else {},
            confidence=best_sim,
            cache_type="semantic",
        )

    async def _load_embedding_index(self) -> None:
        """Load all valid cached embeddings into the in-memory numpy index."""
        rows = await fetch_all(
            self.db_path,
            "SELECT id, query_embedding FROM query_cache "
            "WHERE created_at > ? AND query_embedding IS NOT NULL",
            (self._ttl_cutoff(),),
        )

        embeddings: list[np.ndarray] = []
        ids: list[str] = []

        for row in rows:
            try:
                emb = np.frombuffer(row["query_embedding"], dtype=np.float32)
                if len(emb) == settings.OLLAMA_EMBED_DIM:
                    embeddings.append(emb)
                    ids.append(row["id"])
            except Exception:
                continue

        if embeddings:
            self._embeddings_cache = np.stack(embeddings)
        else:
            self._embeddings_cache = np.empty((0, settings.OLLAMA_EMBED_DIM), dtype=np.float32)

        self._ids_cache = ids
        self._loaded = True

    def _normalize_query(self, query: str) -> str:
        """Normalize query for consistent hashing: lowercase, collapse whitespace, strip punctuation."""
        normalized = query.lower().strip()
        normalized = re.sub(r"\s+", " ", normalized)
        normalized = normalized.rstrip("?!.")
        return normalized

    @staticmethod
    def _entities_match(new_query: str, cached_query: str) -> bool:
        """
        ADDITIVE check: reject semantic cache hit if key entities differ.

        Extracts semester numbers, batch years, and student names from both queries.
        Returns True (allow hit) if entities are compatible, False (reject) if they differ.

        This can only REJECT a hit — it never overrides a cache miss.
        """
        new_lower = new_query.lower()
        cached_lower = cached_query.lower()

        # Extract semester numbers
        new_sems = set(re.findall(r"semester\s*(\d+)", new_lower))
        new_sems.update(re.findall(r"\bsem\s*(\d+)", new_lower))
        cached_sems = set(re.findall(r"semester\s*(\d+)", cached_lower))
        cached_sems.update(re.findall(r"\bsem\s*(\d+)", cached_lower))
        if new_sems and cached_sems and new_sems != cached_sems:
            return False

        # Extract batch years
        new_batches = set(re.findall(r"batch\s*(\d{4})", new_lower))
        cached_batches = set(re.findall(r"batch\s*(\d{4})", cached_lower))
        if new_batches and cached_batches and new_batches != cached_batches:
            return False

        # Extract standalone numbers that look like semesters (1-8) after "in" or "for"
        new_nums = set(re.findall(r"(?:in|for)\s+(\d)\b", new_lower))
        cached_nums = set(re.findall(r"(?:in|for)\s+(\d)\b", cached_lower))
        if new_nums and cached_nums and new_nums != cached_nums:
            return False

        # Extract "top N" — different N means different query
        new_top = re.search(r"top\s*(\d+)", new_lower)
        cached_top = re.search(r"top\s*(\d+)", cached_lower)
        if new_top and cached_top and new_top.group(1) != cached_top.group(1):
            return False

        # Extract branch names
        branches = ["cse", "ece", "me", "mechanical", "computer science", "electronics"]
        new_branches = {b for b in branches if b in new_lower}
        cached_branches = {b for b in branches if b in cached_lower}
        if new_branches and cached_branches and new_branches != cached_branches:
            return False

        return True

    def _ttl_cutoff(self) -> str:
        """ISO timestamp for the TTL cutoff (now - TTL hours)."""
        cutoff = datetime.utcnow() - timedelta(hours=self.ttl_hours)
        return cutoff.isoformat()

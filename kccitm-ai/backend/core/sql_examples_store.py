"""
Stores verified (question, SQL) pairs for dynamic few-shot retrieval.

Uses SQLite + numpy for embedding search (same pattern as QueryCache).
At inference time, retrieves the top 2-3 most similar past queries
and injects them as few-shot examples into the SQL prompt.
"""

import json
import logging
import uuid
from typing import Optional

import numpy as np

from config import settings
from core.llm_client import OllamaClient
from db.sqlite_client import execute, fetch_all, fetch_one

logger = logging.getLogger(__name__)

# Store in feedback.db alongside training_candidates
_DB_PATH = settings.FEEDBACK_DB
EMBED_DIM = 768  # nomic-embed-text

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS sql_examples (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    sql_text TEXT NOT NULL,
    reasoning TEXT DEFAULT '',
    source TEXT DEFAULT 'seed',
    embedding BLOB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


class SQLExamplesStore:
    """Manages verified SQL examples for few-shot retrieval via SQLite + numpy."""

    def __init__(self, llm: OllamaClient):
        self.llm = llm
        self._embeddings: np.ndarray | None = None
        self._ids: list[str] = []
        self._loaded = False

    async def ensure_table(self):
        """Create the sql_examples table if it doesn't exist."""
        await execute(_DB_PATH, _INIT_SQL)

    async def add_example(
        self, question: str, sql: str, reasoning: str = "", source: str = "seed"
    ):
        """Add a verified (question, SQL) pair to the store."""
        await self.ensure_table()
        try:
            embedding = await self.llm.embed(question)
            embedding_blob = np.array(embedding, dtype=np.float32).tobytes()

            await execute(
                _DB_PATH,
                """INSERT OR IGNORE INTO sql_examples
                   (id, question, sql_text, reasoning, source, embedding)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), question, sql, reasoning, source, embedding_blob),
            )
            self._loaded = False  # invalidate cache
        except Exception as e:
            logger.debug("Failed to add SQL example: %s", e)

    async def find_similar(self, question: str, top_k: int = 3) -> list[dict]:
        """Find the top-k most similar past queries with their SQL."""
        if not self._loaded:
            await self._load_index()

        if self._embeddings is None or len(self._embeddings) == 0:
            return []

        try:
            query_emb = np.array(await self.llm.embed(question), dtype=np.float32)
        except Exception:
            return []

        # Cosine similarity
        query_norm = query_emb / (np.linalg.norm(query_emb) + 1e-8)
        cache_norms = self._embeddings / (
            np.linalg.norm(self._embeddings, axis=1, keepdims=True) + 1e-8
        )
        similarities = cache_norms @ query_norm

        # Get top-k indices
        top_indices = np.argsort(similarities)[-top_k:][::-1]

        examples = []
        for idx in top_indices:
            sim = float(similarities[idx])
            if sim < 0.5:  # skip low similarity
                continue
            eid = self._ids[idx]
            row = await fetch_one(
                _DB_PATH,
                "SELECT question, sql_text, reasoning FROM sql_examples WHERE id = ?",
                (eid,),
            )
            if row:
                examples.append({
                    "question": row["question"],
                    "sql": row["sql_text"],
                    "reasoning": row.get("reasoning", ""),
                    "similarity": sim,
                })

        return examples

    async def count(self) -> int:
        """Return number of examples in the store."""
        row = await fetch_one(_DB_PATH, "SELECT COUNT(*) as cnt FROM sql_examples")
        return row["cnt"] if row else 0

    async def _load_index(self):
        """Load all embeddings into numpy for fast cosine search."""
        await self.ensure_table()
        rows = await fetch_all(
            _DB_PATH,
            "SELECT id, embedding FROM sql_examples WHERE embedding IS NOT NULL",
        )

        embeddings = []
        ids = []
        for row in rows:
            try:
                emb = np.frombuffer(row["embedding"], dtype=np.float32)
                if len(emb) == EMBED_DIM:
                    embeddings.append(emb)
                    ids.append(row["id"])
            except Exception:
                continue

        if embeddings:
            self._embeddings = np.stack(embeddings)
        else:
            self._embeddings = np.empty((0, EMBED_DIM), dtype=np.float32)

        self._ids = ids
        self._loaded = True
        logger.info("SQL examples index loaded: %d entries", len(ids))

    @staticmethod
    def format_examples_for_prompt(examples: list[dict]) -> str:
        """Format retrieved examples as few-shot prompt text."""
        if not examples:
            return ""

        lines = ["\n=== SIMILAR VERIFIED EXAMPLES (follow these patterns) ==="]
        for i, ex in enumerate(examples, 1):
            lines.append(f"\nEXAMPLE {i}:")
            lines.append(f"Q: {ex['question']}")
            if ex.get("reasoning"):
                reason_lines = [
                    l.strip() for l in ex["reasoning"].split("\n") if l.strip()
                ][:3]
                if reason_lines:
                    lines.append(f"THINKING: {' | '.join(reason_lines)}")
            lines.append(f"SQL: {ex['sql']}")

        lines.append(
            "\n=== NOW ANSWER THE USER'S QUESTION FOLLOWING THE SAME PATTERN ===\n"
        )
        return "\n".join(lines)


# ── Singleton ────────────────────────────────────────────────────────────────

_store: Optional[SQLExamplesStore] = None


def get_sql_examples_store(llm: OllamaClient = None) -> SQLExamplesStore:
    """Get or create the singleton SQL examples store."""
    global _store
    if _store is None:
        if llm is None:
            raise ValueError("Must provide llm on first call")
        _store = SQLExamplesStore(llm)
    return _store

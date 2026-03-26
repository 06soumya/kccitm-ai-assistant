"""
FAQ generator — auto-generates FAQ entries from clusters of repeated successful queries.
Runs daily. FAQs are stored in prompts.db and embedded in the Milvus FAQ collection.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta

from config import settings
from core.llm_client import OllamaClient
from db.milvus_client import MilvusSearchClient
from db.sqlite_client import execute, fetch_all

logger = logging.getLogger(__name__)


class FAQGenerator:
    """
    When 3+ similar queries all have quality_score > 0.7, generate a canonical
    Q&A pair and embed it in the Milvus FAQ collection.
    """

    def __init__(self, llm: OllamaClient, milvus: MilvusSearchClient) -> None:
        self.llm = llm
        self.milvus = milvus
        self.min_cluster_size = 3
        self.min_quality = 0.7

    # ── Public entry point ────────────────────────────────────────────────────

    async def run_generation(self) -> dict:
        """Find clusters of good queries, generate FAQs. Returns summary dict."""
        good_queries = await self._get_good_queries(days=7)
        if not good_queries:
            return {"clusters_found": 0, "faqs_generated": 0, "faqs_updated": 0}

        clusters = self._cluster_queries(good_queries)
        generated = updated = 0

        for cluster in clusters:
            if len(cluster) < self.min_cluster_size:
                continue
            existing = await self._find_existing_faq(cluster[0]["query_text"])
            if existing:
                await self._update_faq_hit(existing["id"])
                updated += 1
                continue
            faq = await self._generate_faq(cluster)
            if faq:
                await self._store_faq(faq)
                generated += 1

        return {
            "clusters_found": len(clusters),
            "faqs_generated": generated,
            "faqs_updated": updated,
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _get_good_queries(self, days: int = 7) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        rows = await fetch_all(
            settings.FEEDBACK_DB,
            """SELECT query_text, response_text, quality_score, route_used
               FROM feedback
               WHERE quality_score >= ? AND created_at > ?
               ORDER BY quality_score DESC""",
            (self.min_quality, cutoff),
        )
        return [dict(r) for r in (rows or [])]

    def _cluster_queries(self, queries: list[dict]) -> list[list[dict]]:
        clusters: list[list[dict]] = []
        used: set[int] = set()
        for i, q1 in enumerate(queries):
            if i in used:
                continue
            cluster = [q1]
            used.add(i)
            words1 = set(q1.get("query_text", "").lower().split())
            for j, q2 in enumerate(queries):
                if j in used or j <= i:
                    continue
                words2 = set(q2.get("query_text", "").lower().split())
                if words1 and words2:
                    jaccard = len(words1 & words2) / len(words1 | words2)
                    if jaccard > 0.4:
                        cluster.append(q2)
                        used.add(j)
            clusters.append(cluster)
        clusters.sort(key=len, reverse=True)
        return clusters

    async def _find_existing_faq(self, query_text: str) -> dict | None:
        existing = await fetch_all(
            settings.PROMPTS_DB,
            "SELECT * FROM faq_entries WHERE status = 'active'",
        )
        for faq in (existing or []):
            q_words = set(query_text.lower().split())
            faq_words = set((faq.get("canonical_question") or "").lower().split())
            if q_words and faq_words:
                jaccard = len(q_words & faq_words) / len(q_words | faq_words)
                if jaccard > 0.5:
                    return dict(faq)
        return None

    async def _generate_faq(self, cluster: list[dict]) -> dict | None:
        best = max(cluster, key=lambda q: q.get("quality_score", 0))
        questions = "\n".join(f"- {q['query_text']}" for q in cluster[:5])

        prompt = (
            f"Given these similar questions asked by faculty about student data, "
            f"create ONE canonical FAQ entry.\n\n"
            f"Similar questions asked:\n{questions}\n\n"
            f"Best answer (quality score {best.get('quality_score', 0):.2f}):\n"
            f"{(best.get('response_text') or '')[:500]}\n\n"
            "Create a clean, canonical Q&A pair. Keep all specific data points from the best answer.\n"
            'Return JSON only:\n{"question": "The canonical question", "answer": "The polished answer"}'
        )
        try:
            response = await self.llm.generate(
                prompt=prompt, temperature=0.2, max_tokens=600, format="json"
            )
            text = response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
            return json.loads(text.strip())
        except Exception as exc:
            logger.warning("FAQ generation failed: %s", exc)
            return None

    async def _store_faq(self, faq: dict) -> None:
        faq_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        await execute(
            settings.PROMPTS_DB,
            """INSERT INTO faq_entries
               (id, canonical_question, answer, status, created_at, updated_at)
               VALUES (?, ?, ?, 'active', ?, ?)""",
            (faq_id, faq["question"], faq["answer"], now, now),
        )

        # Embed in Milvus FAQ collection
        try:
            embedding = await self.llm.embed(faq["question"])
            self.milvus.client.insert("faq", [{
                "faq_id": faq_id,
                "question": faq["question"],
                "answer": faq["answer"],
                "dense": embedding,
            }])
            logger.info("FAQ '%s...' stored in Milvus", faq["question"][:50])
        except Exception as exc:
            logger.warning("Milvus FAQ insert failed: %s", exc)

    async def _update_faq_hit(self, faq_id: str) -> None:
        await execute(
            settings.PROMPTS_DB,
            "UPDATE faq_entries SET hit_count = hit_count + 1, updated_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), faq_id),
        )

"""
Multi-query expansion with Reciprocal Rank Fusion for KCCITM AI Assistant.

Rewrites a query into 3 variants to cast a wider retrieval net, then merges
results from all variants using RRF — chunks appearing across multiple result
lists get boosted, improving precision for ambiguous queries.

Usage:
    expander = MultiQueryExpander(llm)
    variants = await expander.expand("top CSE students")
    # ["best Computer Science students by academic performance",
    #  "highest SGPA students in Computer Science and Engineering",
    #  "rank CSE branch students by marks"]

    merged = MultiQueryExpander.reciprocal_rank_fusion([list1, list2, list3])
"""

import json
import logging
import re
from collections import defaultdict

from core.llm_client import OllamaClient

logger = logging.getLogger(__name__)

MULTI_QUERY_PROMPT = """Rewrite the following question about student academic data in 3 different ways. Each rewrite should emphasize a different aspect of the question.

Rules:
- Each variant should be a complete, self-contained question
- Vary the vocabulary: use synonyms, rephrase, change structure
- If the original mentions abbreviations (CSE, ECE), expand them in at least one variant
- If the original is vague, make variants more specific
- Keep each variant under 30 words

Original question: {query}

Return ONLY a JSON array of exactly 3 strings. No explanation, no markdown:
["variant 1", "variant 2", "variant 3"]"""


class MultiQueryExpander:
    """
    Expands a single query into multiple variants and merges results via RRF.

    Example expansion:
      Original: "students with poor programming skills"
      Variant 1: "students who scored low in programming subjects"
      Variant 2: "students with C or D grade in coding and algorithms courses"
      Variant 3: "weak performers in KCS101T KCS301 KCS503"
    """

    def __init__(self, llm: OllamaClient) -> None:
        self.llm = llm

    async def expand(self, query: str) -> list[str]:
        """
        Generate 3 query variants.

        Returns:
            List of variant query strings (does NOT include the original).
            Returns [] on failure — pipeline continues with just original query.
        """
        prompt = MULTI_QUERY_PROMPT.format(query=query)
        try:
            response = await self.llm.generate(
                prompt=prompt,
                temperature=0.4,
                max_tokens=250,
                format="json",
            )
            variants = self._parse_variants(response)
            logger.debug("Multi-query expanded to %d variants", len(variants))
            return variants
        except Exception as exc:
            logger.warning("Multi-query expansion failed: %s", exc)
            return []

    def _parse_variants(self, response: str) -> list[str]:
        """
        Parse LLM response into list of variant strings.
        Handles: markdown fences, dict-wrapped arrays, malformed output.
        """
        text = response.strip()

        # Strip markdown fences
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        text = text.strip()

        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if v and str(v).strip()][:3]
            if isinstance(parsed, dict):
                for key in ("variants", "queries", "rewrites", "questions"):
                    if key in parsed:
                        return [str(v).strip() for v in parsed[key]][:3]
        except json.JSONDecodeError:
            pass

        # Fallback: extract quoted strings (≥10 chars to avoid noise)
        matches = re.findall(r'"([^"]{10,})"', text)
        return matches[:3]

    # ── Reciprocal Rank Fusion ────────────────────────────────────────────────

    @staticmethod
    def reciprocal_rank_fusion(
        result_lists: list[list[dict]],
        k: int = 60,
        id_field: str = "chunk_id",
    ) -> list[dict]:
        """
        Merge multiple ranked result lists using Reciprocal Rank Fusion.

        RRF score for document d = Σ 1 / (k + rank(d))
        Chunks appearing across multiple lists get boosted scores.

        Args:
            result_lists: List of search result lists, each [{chunk_id, text, metadata, score}, ...]
            k: RRF smoothing parameter (standard default: 60)
            id_field: Field used for deduplication

        Returns:
            Merged, deduplicated list sorted by RRF score descending.
            Each chunk gets a 'rrf_score' field.
        """
        scores: dict[str, float] = defaultdict(float)
        docs: dict[str, dict] = {}

        for result_list in result_lists:
            for rank, doc in enumerate(result_list):
                doc_id = doc.get(id_field)
                if not doc_id:
                    continue
                scores[doc_id] += 1.0 / (k + rank + 1)
                # Keep the doc with the highest individual score
                if doc_id not in docs or doc.get("score", 0) > docs[doc_id].get("score", 0):
                    docs[doc_id] = doc

        sorted_ids = sorted(scores.keys(), key=lambda d: scores[d], reverse=True)

        merged = []
        for doc_id in sorted_ids:
            doc = docs[doc_id].copy()
            doc["rrf_score"] = scores[doc_id]
            merged.append(doc)

        return merged

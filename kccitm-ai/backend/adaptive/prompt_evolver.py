"""
Prompt evolution engine — analyzes accumulated failure patterns and proposes
prompt modifications. Runs weekly as a batch job.

Flow:
1. Collect failed queries from the past week (quality_score < 0.5)
2. Cluster by Jaccard word-overlap similarity (>40%)
3. For clusters with 3+ queries, ask LLM to analyze the pattern
4. LLM proposes a specific prompt modification (JSON)
5. Store proposal for admin review (status = 'pending')
6. On approval, PromptABTester creates a new version for A/B testing
"""

import json
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta

from config import settings
from core.llm_client import OllamaClient
from db.sqlite_client import execute, fetch_all

logger = logging.getLogger(__name__)


class PromptEvolver:

    def __init__(self, llm: OllamaClient) -> None:
        self.llm = llm
        self.min_cluster_size = 3

    # ── Public entry point ────────────────────────────────────────────────────

    async def run_evolution(self) -> dict:
        """
        Analyze past week's failures and generate prompt-change proposals.

        Returns:
            {"clusters_found": N, "proposals_generated": N, "proposals": [...]}
        """
        failures = await self._get_recent_failures(days=7)
        if not failures:
            return {"clusters_found": 0, "proposals_generated": 0, "proposals": []}

        clusters = self._cluster_failures(failures)

        proposals = []
        for cluster in clusters:
            if len(cluster) < self.min_cluster_size:
                continue
            proposal = await self._generate_proposal(cluster)
            if proposal:
                proposal_id = await self._store_proposal(proposal)
                proposal["id"] = proposal_id
                proposals.append(proposal)

        return {
            "clusters_found": len(clusters),
            "proposals_generated": len(proposals),
            "proposals": proposals,
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _get_recent_failures(self, days: int = 7) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        rows = await fetch_all(
            settings.FEEDBACK_DB,
            """SELECT query_text, response_text, route_used, quality_score,
                      sql_generated, feedback_text
               FROM feedback
               WHERE quality_score IS NOT NULL AND quality_score < 0.5
                 AND created_at > ?
               ORDER BY quality_score ASC""",
            (cutoff,),
        )
        return [dict(r) for r in (rows or [])]

    def _cluster_failures(self, failures: list[dict]) -> list[list[dict]]:
        """Simple Jaccard keyword-overlap clustering (> 40% similarity)."""
        clusters: list[list[dict]] = []
        used: set[int] = set()

        for i, f1 in enumerate(failures):
            if i in used:
                continue
            cluster = [f1]
            used.add(i)
            words1 = set(f1.get("query_text", "").lower().split())

            for j, f2 in enumerate(failures):
                if j in used or j <= i:
                    continue
                words2 = set(f2.get("query_text", "").lower().split())
                if words1 and words2:
                    jaccard = len(words1 & words2) / len(words1 | words2)
                    if jaccard > 0.4:
                        cluster.append(f2)
                        used.add(j)

            clusters.append(cluster)

        clusters.sort(key=len, reverse=True)
        return clusters

    async def _generate_proposal(self, cluster: list[dict]) -> dict | None:
        """Ask LLM to analyze a failure cluster and propose one prompt fix."""
        examples = "\n".join(
            f"- Query: \"{f.get('query_text', '')}\" | Route: {f.get('route_used', '')} | Score: {f.get('quality_score', 0):.2f}"
            for f in cluster[:5]
        )

        # Abbreviated current prompts for context
        prompts = await fetch_all(
            settings.PROMPTS_DB,
            "SELECT prompt_name, section_name, content FROM prompt_templates WHERE is_active = 1",
        )
        prompt_summary = "\n".join(
            f"[{p['prompt_name']}/{p['section_name']}]: {(p['content'] or '')[:200]}..."
            for p in (prompts or [])
        )

        analysis_prompt = (
            f"Analyze these {len(cluster)} failed queries from a student academic data AI assistant "
            f"and suggest a specific prompt improvement.\n\n"
            f"Failed queries (all scored below 0.5 quality):\n{examples}\n\n"
            f"Current system prompts (abbreviated):\n{prompt_summary}\n\n"
            "Based on the failure pattern, suggest ONE specific modification to ONE prompt section.\n"
            "Return JSON only:\n"
            '{"target_prompt": "router|sql_generator|response_generator|hyde|multi_query|compressor", '
            '"target_section": "system|examples|rules|persona", '
            '"action": "add|modify", '
            '"content": "The specific text to add or the modified section text", '
            '"reasoning": "Why this change would fix the failure pattern"}'
        )

        try:
            response = await self.llm.generate(
                prompt=analysis_prompt,
                temperature=0.3,
                max_tokens=500,
                format="json",
            )
            text = response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
            proposal = json.loads(text.strip())
            proposal["cluster_size"] = len(cluster)
            proposal["sample_queries"] = [f.get("query_text", "") for f in cluster[:3]]
            return proposal
        except Exception as exc:
            logger.warning("Proposal generation failed: %s", exc)
            return None

    async def _store_proposal(self, proposal: dict) -> str:
        """Store a proposal for admin review."""
        proposal_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        await execute(
            settings.PROMPTS_DB,
            """INSERT INTO prompt_evolution_log
               (id, prompt_name, section_name, change_reason, change_diff, approved_by, created_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
            (
                proposal_id,
                proposal.get("target_prompt", "unknown"),
                proposal.get("target_section", "unknown"),
                proposal.get("reasoning", ""),
                json.dumps(proposal),
                now,
            ),
        )
        return proposal_id

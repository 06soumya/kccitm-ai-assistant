"""
A/B testing manager for prompt versions.

When admin approves a proposal, a new prompt version is created alongside the old one.
Traffic is split 70% new / 30% old.  After MIN_QUERIES_FOR_DECISION queries on each,
the better version wins and the loser is deactivated.
"""

import logging
import random
import uuid
from datetime import datetime

from config import settings
from db.sqlite_client import execute, fetch_all, fetch_one

logger = logging.getLogger(__name__)


class PromptABTester:

    TRAFFIC_SPLIT_NEW = 0.7
    MIN_QUERIES_FOR_DECISION = 50
    MIN_IMPROVEMENT = 0.05   # New must beat old by this margin

    # ── Prompt retrieval ──────────────────────────────────────────────────────

    async def get_active_prompt(self, prompt_name: str, section_name: str) -> tuple[str, int]:
        """
        Return (content, version) respecting A/B traffic split.
        Falls back to ("", 0) if no prompt found.
        """
        versions = await fetch_all(
            settings.PROMPTS_DB,
            """SELECT id, content, version, query_count, performance_score
               FROM prompt_templates
               WHERE prompt_name = ? AND section_name = ? AND is_active = 1
               ORDER BY version DESC LIMIT 2""",
            (prompt_name, section_name),
        )
        if not versions:
            return ("", 0)

        if len(versions) == 1:
            v = versions[0]
            return (v["content"], v["version"])

        # A/B test — two active versions
        chosen = versions[0] if random.random() < self.TRAFFIC_SPLIT_NEW else versions[1]
        await execute(
            settings.PROMPTS_DB,
            "UPDATE prompt_templates SET query_count = query_count + 1 WHERE id = ?",
            (chosen["id"],),
        )
        return (chosen["content"], chosen["version"])

    # ── Version management ────────────────────────────────────────────────────

    async def create_new_version(
        self,
        prompt_name: str,
        section_name: str,
        content: str,
        reason: str = "",
    ) -> int:
        """Create a new prompt version (old stays active → A/B test begins)."""
        current = await fetch_one(
            settings.PROMPTS_DB,
            "SELECT MAX(version) AS max_v FROM prompt_templates WHERE prompt_name = ? AND section_name = ?",
            (prompt_name, section_name),
        )
        new_version = (current["max_v"] if current and current["max_v"] else 0) + 1

        await execute(
            settings.PROMPTS_DB,
            """INSERT INTO prompt_templates
               (id, prompt_name, section_name, content, version, is_active, query_count, created_at)
               VALUES (?, ?, ?, ?, ?, 1, 0, ?)""",
            (str(uuid.uuid4()), prompt_name, section_name, content, new_version,
             datetime.utcnow().isoformat()),
        )
        logger.info("New prompt version v%d created for %s/%s", new_version, prompt_name, section_name)
        return new_version

    async def rollback_prompt(self, prompt_name: str, section_name: str) -> bool:
        """Deactivate the newest version (manual rollback)."""
        versions = await fetch_all(
            settings.PROMPTS_DB,
            """SELECT id, version FROM prompt_templates
               WHERE prompt_name = ? AND section_name = ? AND is_active = 1
               ORDER BY version DESC""",
            (prompt_name, section_name),
        )
        if not versions:
            return False
        await execute(
            settings.PROMPTS_DB,
            "UPDATE prompt_templates SET is_active = 0 WHERE id = ?",
            (versions[0]["id"],),
        )
        logger.info("Rolled back %s/%s from v%d", prompt_name, section_name, versions[0]["version"])
        return True

    # ── Evaluation ────────────────────────────────────────────────────────────

    async def evaluate_tests(self) -> list[dict]:
        """
        Evaluate all active A/B tests. Promote winners, retire losers.
        Returns list of decision dicts.
        """
        decisions: list[dict] = []

        pairs = await fetch_all(
            settings.PROMPTS_DB,
            """SELECT prompt_name, section_name, COUNT(*) AS cnt
               FROM prompt_templates WHERE is_active = 1
               GROUP BY prompt_name, section_name HAVING cnt >= 2""",
        )

        for pair in (pairs or []):
            versions = await fetch_all(
                settings.PROMPTS_DB,
                """SELECT id, version, query_count, performance_score
                   FROM prompt_templates
                   WHERE prompt_name = ? AND section_name = ? AND is_active = 1
                   ORDER BY version DESC""",
                (pair["prompt_name"], pair["section_name"]),
            )
            if len(versions) < 2:
                continue

            new_v, old_v = versions[0], versions[1]
            if (new_v["query_count"] or 0) < self.MIN_QUERIES_FOR_DECISION:
                continue
            if (old_v["query_count"] or 0) < self.MIN_QUERIES_FOR_DECISION:
                continue

            new_score = new_v["performance_score"] or 0
            old_score = old_v["performance_score"] or 0

            if new_score >= old_score + self.MIN_IMPROVEMENT:
                await execute(settings.PROMPTS_DB,
                    "UPDATE prompt_templates SET is_active = 0 WHERE id = ?", (old_v["id"],))
                decisions.append({
                    "prompt": pair["prompt_name"], "section": pair["section_name"],
                    "winner": f"v{new_v['version']}", "loser": f"v{old_v['version']}",
                    "new_score": new_score, "old_score": old_score,
                })
            elif old_score > new_score:
                await execute(settings.PROMPTS_DB,
                    "UPDATE prompt_templates SET is_active = 0 WHERE id = ?", (new_v["id"],))
                decisions.append({
                    "prompt": pair["prompt_name"], "section": pair["section_name"],
                    "winner": f"v{old_v['version']} (rollback)", "loser": f"v{new_v['version']}",
                    "new_score": new_score, "old_score": old_score,
                })

        return decisions

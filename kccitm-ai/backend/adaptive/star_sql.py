"""
STaR-SQL: Self-Taught Reasoner for Text-to-SQL (ACL 2025)

The loop:
1. User asks query -> model generates SQL + reasoning chain
2. SQL executes -> check result
3. If CORRECT (thumbs up):
   -> Save (query, reasoning_chain, sql) as training candidate
4. If WRONG (thumbs down):
   -> Admin provides correct SQL (or system finds it via healing)
   -> Ask model: "Given this correct SQL, generate the reasoning that leads to it"
   -> Save (query, rationalized_chain, correct_sql) as training candidate
5. Periodically fine-tune on all collected training pairs
"""

import json
import logging
import re
import uuid
from typing import Optional

from config import settings
from db.sqlite_client import execute, fetch_all, fetch_one

logger = logging.getLogger(__name__)


class StarSQLTrainer:
    """Implements STaR-SQL self-training loop."""

    def __init__(self, llm_client):
        self.llm = llm_client

    async def rationalize(
        self, query: str, correct_sql: str, schema_context: str = "",
    ) -> Optional[dict]:
        """
        Given a query and the CORRECT SQL, ask the model to generate
        the reasoning chain that would have led to this SQL.

        This is the key STaR insight: learn from failures by
        reverse-engineering the reasoning.
        """
        prompt = f"""You are a SQL reasoning expert. A user asked a question and the correct SQL answer is provided below.

Your job: Generate the step-by-step reasoning chain that logically leads from the question to the correct SQL.

{schema_context}

USER QUESTION: {query}

CORRECT SQL: {correct_sql}

Generate the reasoning chain in this exact format:

THINKING:
Step 1 - UNDERSTAND: [What the user is asking in plain language]
Step 2 - TABLES: [Which tables are needed and why]
Step 3 - COLUMNS: [Which columns to select and why]
Step 4 - FILTERS: [What WHERE conditions and why]
Step 5 - CALCULATIONS: [Any math/aggregation needed and why]
Step 6 - GROUPING: [GROUP BY needed? Why?]
Step 7 - ORDERING: [ORDER BY and LIMIT reasoning]
Step 8 - SQL: [The final SQL query]

Be specific. Reference actual table and column names. Explain WHY each step is needed."""

        try:
            response = await self.llm.generate(prompt, temperature=0.15)
            return {
                "query": query,
                "correct_sql": correct_sql,
                "reasoning_chain": response.strip(),
                "source": "rationalization",
            }
        except Exception as e:
            logger.error("Rationalization failed: %s", e)
            return None

    async def process_failure(
        self,
        query: str,
        correct_sql: str,
        original_wrong_sql: str = "",
        feedback_text: str = "",
        schema_context: str = "",
    ) -> Optional[str]:
        """
        Process a failed query through the STaR loop:
        1. Rationalize: generate reasoning for the correct SQL
        2. Save as training candidate
        3. Return the training candidate ID
        """
        result = await self.rationalize(query, correct_sql, schema_context)
        if not result:
            return None

        candidate_id = str(uuid.uuid4())

        training_entry = json.dumps({
            "type": "star_rationalization",
            "query": query,
            "correct_sql": correct_sql,
            "original_wrong_sql": original_wrong_sql,
            "reasoning_chain": result["reasoning_chain"],
            "feedback_text": feedback_text,
        })

        await execute(
            settings.FEEDBACK_DB,
            """INSERT OR IGNORE INTO training_candidates
               (id, query, response, quality_score, category, source, created_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
            (candidate_id, query, training_entry, 0.85, "sql_gen", "star_rationalization"),
        )

        logger.info("STaR training candidate created: %s for: %s", candidate_id, query[:50])
        return candidate_id

    async def process_success(
        self,
        query: str,
        sql: str,
        reasoning_chain: str = "",
        schema_context: str = "",
    ) -> Optional[str]:
        """
        Process a successful query (thumbs up):
        If reasoning chain exists, save directly.
        If not, generate one from the correct SQL.
        """
        if not reasoning_chain:
            result = await self.rationalize(query, sql, schema_context)
            if result:
                reasoning_chain = result["reasoning_chain"]

        candidate_id = str(uuid.uuid4())

        training_entry = json.dumps({
            "type": "star_success",
            "query": query,
            "correct_sql": sql,
            "reasoning_chain": reasoning_chain,
        })

        await execute(
            settings.FEEDBACK_DB,
            """INSERT OR IGNORE INTO training_candidates
               (id, query, response, quality_score, category, source, created_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
            (candidate_id, query, training_entry, 0.95, "sql_gen", "star_success"),
        )

        logger.info("STaR success candidate: %s", candidate_id)
        return candidate_id

    async def batch_rationalize_from_healing(self) -> dict:
        """
        Go through approved healing entries that have correct SQL
        and generate rationalized training data from each.
        """
        approved = await fetch_all(
            settings.FEEDBACK_DB,
            """SELECT h.id, h.query, h.fix_details, h.failure_type, h.change_reason
               FROM healing_queue h
               WHERE h.status = 'approved'
               AND h.id NOT IN (
                   SELECT DISTINCT query FROM training_candidates
                   WHERE source = 'star_rationalization'
               )""",
        )

        results = {"processed": 0, "succeeded": 0, "failed": 0}

        for entry in approved:
            query = entry.get("query", "")
            if not query:
                continue

            results["processed"] += 1

            correct_sql = self._extract_sql_from_fix(entry.get("fix_details", ""))
            if correct_sql:
                cid = await self.process_failure(
                    query=query,
                    correct_sql=correct_sql,
                    feedback_text=entry.get("change_reason", ""),
                )
                if cid:
                    results["succeeded"] += 1
                else:
                    results["failed"] += 1
            else:
                results["failed"] += 1

        logger.info("Batch rationalization: %s", results)
        return results

    @staticmethod
    def _extract_sql_from_fix(fix_text: str) -> Optional[str]:
        """Try to extract a SQL query from fix details text."""
        if not fix_text:
            return None

        # Try JSON first
        try:
            data = json.loads(fix_text)
            if isinstance(data, dict):
                for key in ("correct_sql", "sql", "fixed_sql"):
                    if key in data and data[key]:
                        return data[key]
        except (json.JSONDecodeError, TypeError):
            pass

        # Regex patterns
        patterns = [
            r"```sql\s*(.*?)\s*```",
            r"SQL:\s*(SELECT.*?)(?:\n\n|\Z)",
            r"(SELECT\s+.+?)(?:;|\Z)",
        ]
        for pattern in patterns:
            match = re.search(pattern, fix_text, re.DOTALL | re.IGNORECASE)
            if match:
                sql = match.group(1).strip().rstrip(";")
                if sql.upper().startswith("SELECT"):
                    return sql

        return None

    async def get_training_stats(self) -> dict:
        """Get current training data statistics."""
        total = await fetch_one(
            settings.FEEDBACK_DB, "SELECT COUNT(*) as c FROM training_candidates"
        )

        by_source = await fetch_all(
            settings.FEEDBACK_DB,
            "SELECT source, COUNT(*) as c FROM training_candidates GROUP BY source",
        )

        count = total["c"] if total else 0
        return {
            "total": count,
            "target": 500,
            "by_source": {r["source"]: r["c"] for r in by_source} if by_source else {},
            "progress_pct": round((count / 500) * 100, 1) if count else 0,
            "ready_for_lora": count >= 500,
        }

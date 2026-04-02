"""
Seed the sql_examples table from existing training_candidates.

Reads verified (question, SQL) pairs from feedback.db and embeds them
for few-shot retrieval at inference time.

Usage: python -m tools.seed_sql_examples
"""

import asyncio
import json
import logging

from config import settings
from core.llm_client import OllamaClient
from core.sql_examples_store import get_sql_examples_store
from db.sqlite_client import fetch_all, fetch_one

logging.basicConfig(level=logging.INFO, format="%(message)s")


async def seed():
    llm = OllamaClient()
    store = get_sql_examples_store(llm)
    await store.ensure_table()

    # Check if already seeded
    existing = await store.count()
    if existing > 0:
        print(f"Already have {existing} examples. Delete sql_examples table to re-seed.")
        return

    rows = await fetch_all(
        settings.FEEDBACK_DB,
        "SELECT query, response, source FROM training_candidates",
    )

    if not rows:
        print("No training candidates found")
        return

    added = 0
    skipped = 0

    for row in rows:
        query = row.get("query", "")
        if not query:
            skipped += 1
            continue

        raw_response = row.get("response", "")
        sql = ""
        reasoning = ""

        # Parse JSON response formats
        try:
            data = json.loads(raw_response)
            sql = data.get("correct_sql", "") or data.get("sql", "")
            reasoning = data.get("reasoning_chain", "") or data.get("reasoning", "")
        except (json.JSONDecodeError, TypeError):
            if raw_response.strip().upper().startswith("SELECT"):
                sql = raw_response.strip()

        if not sql or not sql.strip().upper().startswith("SELECT"):
            skipped += 1
            continue

        source = row.get("source", "unknown")
        await store.add_example(query, sql, reasoning, source)
        added += 1

        if added % 50 == 0:
            print(f"  Seeded {added} examples...")

    total = await store.count()
    print(f"\nDone: {added} added, {skipped} skipped")
    print(f"Total in sql_examples table: {total}")


if __name__ == "__main__":
    asyncio.run(seed())

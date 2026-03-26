"""
Seed the prompts database with v1 system prompts.

Run once after Phase 1 setup (SQLite databases must already be initialized):

    cd backend
    python -m ingestion.init_prompts

Safe to run multiple times — skips prompts that already exist.
"""

import asyncio
import uuid

from db.sqlite_client import execute, fetch_one
from config import settings
from core.router import ROUTER_SYSTEM_PROMPT
from core.sql_pipeline import SQL_GENERATOR_SYSTEM_PROMPT
from core.rag_pipeline import RESPONSE_GENERATOR_PROMPT
from core.hyde import HYDE_PROMPT
from core.multi_query import MULTI_QUERY_PROMPT
from core.compressor import COMPRESSION_PROMPT

INITIAL_PROMPTS = [
    {
        "prompt_name": "router",
        "section_name": "system",
        "content": ROUTER_SYSTEM_PROMPT,
    },
    {
        "prompt_name": "sql_generator",
        "section_name": "system",
        "content": SQL_GENERATOR_SYSTEM_PROMPT,
    },
    {
        "prompt_name": "response_generator",
        "section_name": "system",
        "content": RESPONSE_GENERATOR_PROMPT,
    },
    {
        "prompt_name": "response_generator",
        "section_name": "persona",
        "content": RESPONSE_GENERATOR_PROMPT,
    },
    {
        "prompt_name": "hyde",
        "section_name": "system",
        "content": HYDE_PROMPT,
    },
    {
        "prompt_name": "multi_query",
        "section_name": "system",
        "content": MULTI_QUERY_PROMPT,
    },
    {
        "prompt_name": "compressor",
        "section_name": "system",
        "content": COMPRESSION_PROMPT,
    },
]


async def _update_prompt(prompt_name: str, section_name: str, content: str) -> None:
    """Update an existing prompt row with new content."""
    await execute(
        settings.PROMPTS_DB,
        "UPDATE prompt_templates SET content = ?, version = version + 1 "
        "WHERE prompt_name = ? AND section_name = ? AND is_active = 1",
        (content, prompt_name, section_name),
    )


async def init_prompts() -> None:
    """Insert all initial prompts; skip any that already exist."""
    for prompt in INITIAL_PROMPTS:
        existing = await fetch_one(
            settings.PROMPTS_DB,
            "SELECT id, content FROM prompt_templates "
            "WHERE prompt_name = ? AND section_name = ? AND is_active = 1",
            (prompt["prompt_name"], prompt["section_name"]),
        )
        if existing:
            # Update if still a PLACEHOLDER
            if existing.get("content", "").startswith("PLACEHOLDER"):
                await _update_prompt(
                    prompt["prompt_name"], prompt["section_name"], prompt["content"]
                )
                status = "✓" if "PLACEHOLDER" not in prompt["content"] else "○"
                print(f"  {status} Updated '{prompt['prompt_name']}/{prompt['section_name']}' (was placeholder)")
            else:
                print(
                    f"  — '{prompt['prompt_name']}/{prompt['section_name']}' already exists, skipping"
                )
            continue

        await execute(
            settings.PROMPTS_DB,
            "INSERT INTO prompt_templates "
            "(id, prompt_name, section_name, content, version, is_active) "
            "VALUES (?, ?, ?, ?, 1, 1)",
            (
                str(uuid.uuid4()),
                prompt["prompt_name"],
                prompt["section_name"],
                prompt["content"],
            ),
        )
        status = "✓" if "PLACEHOLDER" not in prompt["content"] else "○"
        print(f"  {status} Stored '{prompt['prompt_name']}/{prompt['section_name']}' v1")

    print(f"\n\033[92m✓ All initial prompts stored in {settings.PROMPTS_DB}\033[0m")


if __name__ == "__main__":
    asyncio.run(init_prompts())

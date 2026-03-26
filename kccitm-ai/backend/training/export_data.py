"""
Export accumulated training candidates as JSONL files for LoRA fine-tuning.
Creates per-category files + a combined file.

Run:
    python -m training.export_data
    python -m training.export_data --category sql_gen --min-score 0.9
"""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from config import settings
from db.sqlite_client import execute, fetch_all

# ── System prompts per category ───────────────────────────────────────────────

ROUTING_SYSTEM = (
    "You are a query classifier for a student academic results database. "
    "Classify the query and extract structured information. "
    "Respond with ONLY JSON."
)

SQL_GEN_SYSTEM = (
    "You are a MySQL query generator for a student academic results database. "
    "Generate ONLY SELECT statements. "
    "Respond with ONLY JSON containing sql, params, and explanation."
)

RESPONSE_SYSTEM = (
    "You are KCCITM AI Assistant, an expert academic data analyst for KCCITM institute. "
    "You help faculty and administrators understand student performance data."
)

_SYSTEM_PROMPTS = {
    "routing": ROUTING_SYSTEM,
    "sql_gen": SQL_GEN_SYSTEM,
    "response": RESPONSE_SYSTEM,
}


# ── Formatting ────────────────────────────────────────────────────────────────

def format_training_entry(query: str, response: str, category: str) -> dict:
    """
    Format a Q&A pair into the Qwen chat-template messages format.

    Returns:
        {"messages": [{"role": "system", ...}, {"role": "user", ...}, {"role": "assistant", ...}]}
    """
    system = _SYSTEM_PROMPTS.get(category, RESPONSE_SYSTEM)
    return {
        "messages": [
            {"role": "system",    "content": system},
            {"role": "user",      "content": query},
            {"role": "assistant", "content": response},
        ]
    }


# ── Export ────────────────────────────────────────────────────────────────────

async def export(
    output_dir: str = "data/training",
    category: str | None = None,
    min_score: float = 0.8,
    exclude_used: bool = True,
) -> dict:
    """
    Export training candidates as JSONL files.

    Args:
        output_dir:   Directory for output files
        category:     Filter by category (routing / sql_gen / response), or None for all
        min_score:    Minimum quality_score threshold
        exclude_used: Skip candidates already marked included_in_training = 1

    Returns:
        {"total": N, "files": {"routing.jsonl": N, ...}}
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    conditions = ["quality_score >= ?"]
    params: list = [min_score]

    if category:
        conditions.append("category = ?")
        params.append(category)

    if exclude_used:
        conditions.append("(included_in_training IS NULL OR included_in_training = 0)")

    where = " AND ".join(conditions)
    rows = await fetch_all(
        settings.FEEDBACK_DB,
        f"SELECT * FROM training_candidates WHERE {where} ORDER BY quality_score DESC",
        params,
    )

    if not rows:
        print("No training candidates found matching criteria.")
        return {"total": 0, "files": {}}

    # Group by category
    by_category: dict[str, list] = {}
    for row in rows:
        cat = row.get("category") or "response"
        by_category.setdefault(cat, []).append(row)

    stats: dict[str, int] = {}
    total = 0
    run_id = datetime.utcnow().strftime("v%Y%m%d_%H%M")

    for cat, entries in by_category.items():
        filename = f"{cat}.jsonl"
        filepath = Path(output_dir) / filename
        with open(filepath, "w", encoding="utf-8") as f:
            for entry in entries:
                formatted = format_training_entry(
                    query=entry["query"] or "",
                    response=entry["response"] or "",
                    category=cat,
                )
                f.write(json.dumps(formatted, ensure_ascii=False) + "\n")
        stats[filename] = len(entries)
        total += len(entries)
        print(f"  Exported {len(entries):>4} entries → {filepath}")

    # Combined file (all categories)
    combined_path = Path(output_dir) / "combined.jsonl"
    with open(combined_path, "w", encoding="utf-8") as f:
        for cat, entries in by_category.items():
            for entry in entries:
                formatted = format_training_entry(
                    entry["query"] or "", entry["response"] or "", cat
                )
                f.write(json.dumps(formatted, ensure_ascii=False) + "\n")
    stats["combined.jsonl"] = total

    # Metadata
    meta = {
        "exported_at": datetime.utcnow().isoformat(),
        "suggested_run_id": run_id,
        "min_score": min_score,
        "total_entries": total,
        "categories": {cat: len(entries) for cat, entries in by_category.items()},
    }
    meta_path = Path(output_dir) / "export_meta.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"\n  Total: {total} entries exported to {output_dir}")
    print(f"  Suggested run ID: {run_id}")
    return {"total": total, "files": stats, "run_id": run_id}


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export LoRA training data")
    parser.add_argument("--output",     default="data/training",  help="Output directory")
    parser.add_argument("--category",   default=None,             help="Filter by category")
    parser.add_argument("--min-score",  type=float, default=0.8,  help="Minimum quality score")
    parser.add_argument("--include-used", action="store_true",    help="Include already-used entries")
    args = parser.parse_args()
    asyncio.run(export(args.output, args.category, args.min_score, not args.include_used))

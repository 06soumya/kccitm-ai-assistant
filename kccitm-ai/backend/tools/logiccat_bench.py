"""
LogicCat MySQL Mini-500 Benchmark Evaluation.

Evaluates our text-to-SQL pipeline against the LogicCat benchmark (500 queries
across 7 MySQL databases, 4 difficulty levels).

For each query:
  1. Read the target database schema dynamically
  2. Generate SQL using our pipeline
  3. Execute both generated + gold SQL
  4. Compare results (execution accuracy)

Usage:
    cd backend
    python -m tools.logiccat_bench                    # Full 500
    python -m tools.logiccat_bench --limit 50         # First 50 only
    python -m tools.logiccat_bench --type 1           # Only type-1 (easy)
    python -m tools.logiccat_bench --db bike           # Only bike database
    python -m tools.logiccat_bench -v                 # Verbose (show SQL)
"""

import argparse
import asyncio
import json
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import aiomysql

from config import settings
from core.llm_client import OllamaClient

logger = logging.getLogger(__name__)

DATASET_PATH = Path.home() / "Desktop" / "LogicCat" / "miniset" / "mysql_mini_500.json"


@dataclass
class BenchResult:
    idx: int
    db_id: str
    difficulty: int
    question: str
    gold_sql: str
    generated_sql: str = ""
    gold_result: list = field(default_factory=list)
    gen_result: list = field(default_factory=list)
    match: bool = False
    gen_error: str = ""
    exec_error: str = ""
    time_ms: float = 0.0


async def get_schema_for_db(db_name: str) -> str:
    """Read schema from a LogicCat database and format as prompt text."""
    pool = await aiomysql.create_pool(
        host=settings.MYSQL_HOST, port=settings.MYSQL_PORT,
        user=settings.MYSQL_USER, password=settings.MYSQL_PASSWORD,
        db=db_name, minsize=1, maxsize=2,
    )
    lines = [f"DATABASE: {db_name}\n"]

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SHOW TABLES")
            tables = [r[0] for r in await cur.fetchall()]

            for table in tables:
                await cur.execute(f"DESCRIBE `{table}`")
                cols = await cur.fetchall()
                lines.append(f"TABLE: {table}")
                lines.append("| Column | Type | Key |")
                lines.append("|--------|------|-----|")
                for col in cols:
                    key = col[3] if col[3] else ""
                    lines.append(f"| {col[0]} | {col[1]} | {key} |")

                # Sample values (3 per column)
                try:
                    await cur.execute(f"SELECT * FROM `{table}` LIMIT 3")
                    sample_rows = await cur.fetchall()
                    col_names = [c[0] for c in cols]
                    if sample_rows:
                        lines.append(f"Sample data ({len(sample_rows)} rows):")
                        for row in sample_rows:
                            vals = ", ".join(f"{col_names[i]}={row[i]}" for i in range(min(5, len(row))))
                            lines.append(f"  {vals}")
                except Exception:
                    pass
                lines.append("")

            # Foreign keys
            await cur.execute("""
                SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
                FROM information_schema.KEY_COLUMN_USAGE
                WHERE REFERENCED_TABLE_NAME IS NOT NULL AND TABLE_SCHEMA = %s
            """, (db_name,))
            fks = await cur.fetchall()
            if fks:
                lines.append("FOREIGN KEYS:")
                for fk in fks:
                    lines.append(f"  {fk[0]}.{fk[1]} -> {fk[2]}.{fk[3]}")

    pool.close()
    await pool.wait_closed()
    return "\n".join(lines)


async def execute_sql_on_db(db_name: str, sql: str) -> list:
    """Execute SQL against a specific LogicCat database."""
    pool = await aiomysql.create_pool(
        host=settings.MYSQL_HOST, port=settings.MYSQL_PORT,
        user=settings.MYSQL_USER, password=settings.MYSQL_PASSWORD,
        db=db_name, minsize=1, maxsize=2,
    )
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql)
                rows = await cur.fetchall()
                return [list(r) for r in rows]
    finally:
        pool.close()
        await pool.wait_closed()


def results_match(gold: list, generated: list) -> bool:
    """Compare two result sets for equivalence (order-independent)."""
    if not gold and not generated:
        return True
    if not gold or not generated:
        return False

    # Normalize: convert to sorted tuples of strings
    def normalize(rows):
        normed = []
        for row in rows:
            normed.append(tuple(
                str(round(v, 2)) if isinstance(v, float) else str(v)
                for v in row
            ))
        return sorted(normed)

    try:
        return normalize(gold) == normalize(generated)
    except Exception:
        return False


SYSTEM_PROMPT_TEMPLATE = """You are a MySQL expert. Generate a SELECT query for the given question.

{schema}

RULES:
1. ONLY generate SELECT statements.
2. Use the exact column and table names from the schema above.
3. Add LIMIT 100 if not specified.
4. Use ROUND() for decimal calculations.

Respond with ONLY a JSON object:
{{"sql": "SELECT ...", "explanation": "brief explanation"}}"""


async def evaluate(
    limit: int = 500,
    type_filter: int = None,
    db_filter: str = None,
    verbose: bool = False,
) -> list[BenchResult]:
    """Run the LogicCat benchmark evaluation."""

    with open(DATASET_PATH) as f:
        dataset = json.load(f)

    # Apply filters
    if type_filter:
        dataset = [e for e in dataset if e["type"] == type_filter]
    if db_filter:
        dataset = [e for e in dataset if e["db_id"] == db_filter]
    dataset = dataset[:limit]

    total = len(dataset)
    print(f"\n\033[94mLogicCat MySQL Mini-500 Benchmark\033[0m")
    print(f"Evaluating {total} queries | Model: {settings.OLLAMA_MODEL}")
    print(f"{'='*70}\n")

    llm = OllamaClient()
    results: list[BenchResult] = []
    schema_cache: dict[str, str] = {}

    for i, entry in enumerate(dataset):
        db_id = entry["db_id"]
        question = entry["question"]
        gold_sql = entry["query"]
        difficulty = entry["type"]

        br = BenchResult(
            idx=entry["idx"], db_id=db_id, difficulty=difficulty,
            question=question, gold_sql=gold_sql,
        )

        t0 = time.time()

        try:
            # Get schema (cached)
            if db_id not in schema_cache:
                schema_cache[db_id] = await get_schema_for_db(db_id)

            schema_text = schema_cache[db_id]
            system = SYSTEM_PROMPT_TEMPLATE.format(schema=schema_text)

            # Generate SQL
            response = await llm.generate(
                prompt=f"Question: {question}",
                system=system,
                temperature=0.05,
                max_tokens=500,
                format="json",
            )

            # Parse response
            text = response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]

            try:
                data = json.loads(text)
                br.generated_sql = data.get("sql", "").strip()
            except json.JSONDecodeError:
                import re
                match = re.search(r"(SELECT\s+.+?)(?:;|\Z)", text, re.IGNORECASE | re.DOTALL)
                br.generated_sql = match.group(1).strip() if match else ""

            if not br.generated_sql:
                br.gen_error = "Empty SQL generated"
                br.time_ms = (time.time() - t0) * 1000
                results.append(br)
                continue

            # Execute gold SQL
            try:
                br.gold_result = await execute_sql_on_db(db_id, gold_sql)
            except Exception as e:
                br.exec_error = f"Gold SQL error: {e}"

            # Execute generated SQL
            try:
                br.gen_result = await execute_sql_on_db(db_id, br.generated_sql)
                br.match = results_match(br.gold_result, br.gen_result)
            except Exception as e:
                br.exec_error = f"Gen SQL error: {e}"

        except Exception as e:
            br.gen_error = str(e)[:200]

        br.time_ms = (time.time() - t0) * 1000
        results.append(br)

        # Progress
        icon = "\033[92m✓\033[0m" if br.match else "\033[91m✗\033[0m"
        print(f"  {icon} [{i+1:>3}/{total}] [T{difficulty}] [{db_id:<20}] {question[:50]:<50} {br.time_ms:>6.0f}ms")

        if verbose:
            if br.generated_sql:
                print(f"    GEN: {br.generated_sql[:100]}")
            if br.gen_error:
                print(f"    ERR: {br.gen_error[:100]}")
            if br.exec_error:
                print(f"    EXEC: {br.exec_error[:100]}")

    # Summary
    matched = sum(1 for r in results if r.match)
    gen_ok = sum(1 for r in results if r.generated_sql and not r.gen_error)
    exec_ok = sum(1 for r in results if not r.exec_error and r.generated_sql)
    avg_time = sum(r.time_ms for r in results) / total if total else 0

    by_type = {}
    for r in results:
        by_type.setdefault(r.difficulty, []).append(r)

    by_db = {}
    for r in results:
        by_db.setdefault(r.db_id, []).append(r)

    print(f"\n{'='*70}")
    print(f"\033[94mRESULTS — LogicCat MySQL Mini-500\033[0m")
    print(f"{'='*70}")
    print(f"  Model:             {settings.OLLAMA_MODEL}")
    print(f"  Total queries:     {total}")
    print(f"  SQL generated:     {gen_ok}/{total} ({gen_ok/total*100:.1f}%)")
    print(f"  Execution success: {exec_ok}/{total} ({exec_ok/total*100:.1f}%)")
    print(f"  \033[1mExecution accuracy: {matched}/{total} ({matched/total*100:.1f}%)\033[0m")
    print(f"  Avg time:          {avg_time:.0f}ms")

    print(f"\n  By difficulty:")
    for t in sorted(by_type.keys()):
        rs = by_type[t]
        m = sum(1 for r in rs if r.match)
        print(f"    Type {t}: {m}/{len(rs)} ({m/len(rs)*100:.1f}%)")

    print(f"\n  By database:")
    for db in sorted(by_db.keys()):
        rs = by_db[db]
        m = sum(1 for r in rs if r.match)
        print(f"    {db:<25} {m}/{len(rs)} ({m/len(rs)*100:.1f}%)")

    print(f"{'='*70}")

    # Save results
    output = {
        "model": settings.OLLAMA_MODEL,
        "total": total,
        "sql_generated": gen_ok,
        "execution_success": exec_ok,
        "execution_accuracy": matched,
        "accuracy_pct": round(matched / total * 100, 2) if total else 0,
        "avg_time_ms": round(avg_time, 1),
        "by_difficulty": {
            str(t): {"total": len(rs), "matched": sum(1 for r in rs if r.match)}
            for t, rs in by_type.items()
        },
        "by_database": {
            db: {"total": len(rs), "matched": sum(1 for r in rs if r.match)}
            for db, rs in by_db.items()
        },
        "failures": [
            {
                "idx": r.idx, "db_id": r.db_id, "type": r.difficulty,
                "question": r.question[:100],
                "gold_sql": r.gold_sql[:150],
                "gen_sql": r.generated_sql[:150],
                "error": r.gen_error or r.exec_error,
            }
            for r in results if not r.match
        ][:50],
    }

    out_path = Path("data/logiccat_bench_results.json")
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {out_path}")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LogicCat Benchmark Evaluation")
    parser.add_argument("--limit", type=int, default=500, help="Max queries to evaluate")
    parser.add_argument("--type", type=int, help="Filter by difficulty type (1-4)")
    parser.add_argument("--db", type=str, help="Filter by database name")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show generated SQL")
    args = parser.parse_args()

    asyncio.run(evaluate(args.limit, args.type, args.db, args.verbose))

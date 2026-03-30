"""
Dynamic database schema reader — auto-discovers tables, columns, types, keys, and sample values.

Works with ANY MySQL database. Caches the schema until explicitly refreshed.

Usage:
    from core.schema_reader import schema_reader

    schema = await schema_reader.read_schema()
    prompt_text = schema_reader.build_prompt_schema(schema)
"""

import logging
from typing import Optional

from db.mysql_client import get_pool

logger = logging.getLogger(__name__)


class SchemaReader:
    """Reads database schema at runtime — works with ANY MySQL database."""

    def __init__(self):
        self._cache: Optional[dict] = None

    async def read_schema(self) -> dict:
        """Discover full schema: tables, columns, types, keys, sample values, foreign keys, row counts."""
        if self._cache:
            return self._cache

        pool = await get_pool()
        schema: dict = {"tables": [], "foreign_keys": [], "row_counts": {}}

        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # Get all tables
                await cur.execute("SHOW TABLES")
                tables = [row[0] for row in await cur.fetchall()]

                for table in tables:
                    # Get columns
                    await cur.execute(f"DESCRIBE `{table}`")
                    columns = []
                    for row in await cur.fetchall():
                        col = {
                            "name": row[0],
                            "type": row[1],
                            "nullable": row[2] == "YES",
                            "key": row[3] or "",
                            "default": row[4],
                            "extra": row[5] or "",
                        }

                        # Sample 5 distinct values for non-blob columns
                        try:
                            col_type = str(row[1]).upper()
                            if "BLOB" not in col_type and "TEXT" not in col_type:
                                await cur.execute(
                                    f"SELECT DISTINCT `{row[0]}` FROM `{table}` "
                                    f"WHERE `{row[0]}` IS NOT NULL LIMIT 5"
                                )
                                samples = [str(r[0]) for r in await cur.fetchall()]
                                col["samples"] = samples
                            else:
                                col["samples"] = []
                        except Exception:
                            col["samples"] = []

                        columns.append(col)

                    # Row count
                    await cur.execute(f"SELECT COUNT(*) FROM `{table}`")
                    count = (await cur.fetchone())[0]
                    schema["row_counts"][table] = count

                    schema["tables"].append({"name": table, "columns": columns})

                # Foreign keys
                await cur.execute("""
                    SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
                    FROM information_schema.KEY_COLUMN_USAGE
                    WHERE REFERENCED_TABLE_NAME IS NOT NULL
                      AND TABLE_SCHEMA = DATABASE()
                """)
                for row in await cur.fetchall():
                    schema["foreign_keys"].append({
                        "table": row[0],
                        "column": row[1],
                        "ref_table": row[2],
                        "ref_column": row[3],
                    })

        self._cache = schema
        logger.info(
            "Schema loaded: %d tables, %d foreign keys",
            len(schema["tables"]),
            len(schema["foreign_keys"]),
        )
        return schema

    def clear_cache(self):
        """Clear cached schema — next read_schema() call will re-discover."""
        self._cache = None

    def build_prompt_schema(self, schema: dict) -> str:
        """Convert schema dict into a formatted prompt string for the LLM."""
        lines = ["=== DATABASE SCHEMA (auto-discovered) ===\n"]

        for table in schema["tables"]:
            count = schema["row_counts"].get(table["name"], "?")
            lines.append(f"TABLE: {table['name']} ({count:,} rows)")
            lines.append("| Column | Type | Key | Example Values |")
            lines.append("|--------|------|-----|----------------|")

            for col in table["columns"]:
                key = col["key"] if col["key"] else ""
                samples = (
                    ", ".join(f"'{s}'" for s in col["samples"][:3])
                    if col["samples"]
                    else ""
                )
                lines.append(f"| {col['name']} | {col['type']} | {key} | {samples} |")

            lines.append("")

        if schema["foreign_keys"]:
            lines.append("FOREIGN KEYS:")
            for fk in schema["foreign_keys"]:
                lines.append(
                    f"  {fk['table']}.{fk['column']} -> {fk['ref_table']}.{fk['ref_column']}"
                )
            lines.append("")

        return "\n".join(lines)


# Singleton
schema_reader = SchemaReader()

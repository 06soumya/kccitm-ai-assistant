"""
Safe database migration tool with versioning.

Tracks applied migrations in a `schema_migrations` table.
Each migration is a Python file in tools/migrations/.

Usage:
  python -m tools.migrate status          # Show applied / pending
  python -m tools.migrate up              # Apply all pending migrations
  python -m tools.migrate up --dry-run    # Preview without applying
  python -m tools.migrate down <version>  # Rollback to version
  python -m tools.migrate create <name>   # Scaffold a new migration file
"""
import argparse
import sqlite3
import importlib.util
import sys
import time
from datetime import datetime
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
SQLITE_DBS = [
    Path("data/sessions.db"),
    Path("data/cache.db"),
    Path("data/feedback.db"),
    Path("data/prompts.db"),
]
MIGRATIONS_TRACKING_DB = Path("data/schema_migrations.db")


# ── Migration registry ─────────────────────────────────────────────

def _ensure_tracking_db() -> sqlite3.Connection:
    MIGRATIONS_TRACKING_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(MIGRATIONS_TRACKING_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            applied_at  TEXT NOT NULL,
            duration_ms REAL
        )
    """)
    conn.commit()
    return conn


def _applied_versions(conn: sqlite3.Connection) -> set:
    return {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}


def _record_migration(conn: sqlite3.Connection, version: str, name: str, duration_ms: float):
    conn.execute(
        "INSERT OR REPLACE INTO schema_migrations (version, name, applied_at, duration_ms) VALUES (?, ?, ?, ?)",
        (version, name, datetime.utcnow().isoformat(), round(duration_ms, 1))
    )
    conn.commit()


def _remove_migration(conn: sqlite3.Connection, version: str):
    conn.execute("DELETE FROM schema_migrations WHERE version = ?", (version,))
    conn.commit()


# ── Migration file loader ──────────────────────────────────────────

def _discover_migrations() -> list[dict]:
    """Return sorted list of migration dicts: {version, name, path}."""
    MIGRATIONS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(MIGRATIONS_DIR.glob("V*.py"))
    result = []
    for f in files:
        # Filename format: V001__description.py
        stem = f.stem  # e.g. V001__add_feedback_column
        parts = stem.split("__", 1)
        version = parts[0]   # V001
        name    = parts[1].replace("_", " ") if len(parts) > 1 else stem
        result.append({"version": version, "name": name, "path": f})
    return result


def _load_migration(path: Path):
    """Dynamically load a migration module."""
    spec   = importlib.util.spec_from_file_location("migration", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Commands ───────────────────────────────────────────────────────

def cmd_status():
    conn     = _ensure_tracking_db()
    applied  = _applied_versions(conn)
    all_migs = _discover_migrations()

    print(f"\n{'Version':<10} {'Status':<12} {'Name'}")
    print("-" * 55)
    for m in all_migs:
        status = "\033[92mapplied\033[0m" if m["version"] in applied else "\033[93mpending\033[0m"
        print(f"  {m['version']:<10} {status:<20} {m['name']}")

    pending = [m for m in all_migs if m["version"] not in applied]
    applied_list = [m for m in all_migs if m["version"] in applied]
    print(f"\nApplied: {len(applied_list)} | Pending: {len(pending)}")
    conn.close()


def cmd_up(dry_run: bool = False):
    conn     = _ensure_tracking_db()
    applied  = _applied_versions(conn)
    all_migs = _discover_migrations()
    pending  = [m for m in all_migs if m["version"] not in applied]

    if not pending:
        print("\033[92mAll migrations are up to date.\033[0m")
        conn.close()
        return

    print(f"\n{'DRY RUN — ' if dry_run else ''}Applying {len(pending)} pending migration(s)...\n")

    for m in pending:
        print(f"  → {m['version']}: {m['name']}", end="", flush=True)
        if dry_run:
            print(" (skipped — dry run)")
            continue

        start = time.time()
        try:
            mod = _load_migration(m["path"])
            if not hasattr(mod, "up"):
                raise AttributeError(f"{m['path'].name} has no 'up()' function")
            # Pass all SQLite connections to the migration
            db_conns = {p.stem: sqlite3.connect(str(p)) for p in SQLITE_DBS if p.exists()}
            mod.up(db_conns)
            for c in db_conns.values():
                c.commit()
                c.close()
            duration = (time.time() - start) * 1000
            _record_migration(conn, m["version"], m["name"], duration)
            print(f" \033[92m✓ {duration:.0f}ms\033[0m")
        except Exception as e:
            print(f" \033[91m✗ FAILED: {e}\033[0m")
            conn.close()
            sys.exit(1)

    conn.close()
    if not dry_run:
        print(f"\n\033[92mDone.\033[0m")


def cmd_down(target_version: str):
    conn     = _ensure_tracking_db()
    applied  = _applied_versions(conn)
    all_migs = _discover_migrations()

    to_rollback = [
        m for m in reversed(all_migs)
        if m["version"] in applied and m["version"] > target_version
    ]

    if not to_rollback:
        print(f"Nothing to roll back (already at or below {target_version}).")
        conn.close()
        return

    print(f"\nRolling back {len(to_rollback)} migration(s)...\n")
    for m in to_rollback:
        print(f"  ← {m['version']}: {m['name']}", end="", flush=True)
        try:
            mod = _load_migration(m["path"])
            if not hasattr(mod, "down"):
                print(f" \033[93m⚠ no down() — skipping\033[0m")
                continue
            db_conns = {p.stem: sqlite3.connect(str(p)) for p in SQLITE_DBS if p.exists()}
            mod.down(db_conns)
            for c in db_conns.values():
                c.commit()
                c.close()
            _remove_migration(conn, m["version"])
            print(f" \033[92m✓\033[0m")
        except Exception as e:
            print(f" \033[91m✗ FAILED: {e}\033[0m")
            conn.close()
            sys.exit(1)

    conn.close()


def cmd_create(name: str):
    MIGRATIONS_DIR.mkdir(parents=True, exist_ok=True)
    existing = _discover_migrations()
    next_num = len(existing) + 1
    version  = f"V{next_num:03d}"
    slug     = name.lower().replace(" ", "_")
    filename = MIGRATIONS_DIR / f"{version}__{slug}.py"

    template = f'''"""
Migration {version}: {name}

Generated: {datetime.utcnow().strftime("%Y-%m-%d")}
"""
import sqlite3


def up(dbs: dict[str, sqlite3.Connection]) -> None:
    """Apply migration — called with all open SQLite connections."""
    # Example: add a column to feedback.db
    # dbs["feedback"].execute("ALTER TABLE feedback ADD COLUMN new_col TEXT")
    pass


def down(dbs: dict[str, sqlite3.Connection]) -> None:
    """Rollback migration — optional but recommended."""
    pass
'''
    filename.write_text(template)
    print(f"\033[92m✓ Created: {filename}\033[0m")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KCCITM DB Migration Tool")
    sub    = parser.add_subparsers(dest="cmd")

    sub.add_parser("status")

    up_p = sub.add_parser("up")
    up_p.add_argument("--dry-run", action="store_true")

    down_p = sub.add_parser("down")
    down_p.add_argument("version", help="Roll back to this version (e.g. V002)")

    create_p = sub.add_parser("create")
    create_p.add_argument("name", help="Migration name (e.g. 'add feedback column')")

    args = parser.parse_args()

    if args.cmd == "status":
        cmd_status()
    elif args.cmd == "up":
        cmd_up(args.dry_run)
    elif args.cmd == "down":
        cmd_down(args.version)
    elif args.cmd == "create":
        cmd_create(args.name)
    else:
        parser.print_help()

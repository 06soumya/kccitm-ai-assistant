"""
Full system backup and restore.

Usage:
  python -m tools.backup create                   # Timestamped backup
  python -m tools.backup create --output /path    # Custom path
  python -m tools.backup restore backups/20260318_143000.tar.gz
  python -m tools.backup list                     # List available backups
"""
import argparse
import tarfile
import shutil
import subprocess
import json
import sqlite3
from datetime import datetime
from pathlib import Path

BACKUP_DIR = Path("backups")


def _copy_sqlite_dbs(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("sessions.db", "cache.db", "feedback.db", "prompts.db"):
        src = Path(f"data/{name}")
        if src.exists():
            shutil.copy2(src, dest / name)
            print(f"    ✓ {name}: {src.stat().st_size:,} bytes")
        else:
            print(f"    ○ {name} not found — skipping")


def _mysql_dump(dest: Path) -> None:
    try:
        from config import settings
        result = subprocess.run(
            ["mysqldump",
             f"--host={settings.MYSQL_HOST}",
             f"--port={settings.MYSQL_PORT}",
             f"--user={settings.MYSQL_USER}",
             f"--password={settings.MYSQL_PASSWORD}",
             settings.MYSQL_DB],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            (dest / "mysql_dump.sql").write_text(result.stdout)
            print(f"    ✓ MySQL: {len(result.stdout):,} bytes")
            return
    except Exception:
        pass

    # Fallback: docker exec
    try:
        from config import settings
        result = subprocess.run(
            ["docker", "exec", "mysql_kccitm",
             "mysqldump", f"-u{settings.MYSQL_USER}", f"-p{settings.MYSQL_PASSWORD}", settings.MYSQL_DB],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            (dest / "mysql_dump.sql").write_text(result.stdout)
            print(f"    ✓ MySQL (via docker): {len(result.stdout):,} bytes")
            return
    except Exception:
        pass

    print("    ⚠ MySQL dump failed — mysqldump not available or docker not running")


def _export_prompts_json(dest: Path) -> None:
    try:
        conn = sqlite3.connect("data/prompts.db")
        conn.row_factory = sqlite3.Row
        prompts = [dict(r) for r in conn.execute(
            "SELECT * FROM prompt_templates WHERE is_active = 1")]
        faqs = [dict(r) for r in conn.execute(
            "SELECT * FROM faq_entries WHERE status = 'active'")]
        conn.close()
        (dest / "prompts_export.json").write_text(json.dumps(prompts, indent=2, default=str))
        (dest / "faqs_export.json").write_text(json.dumps(faqs, indent=2, default=str))
        print(f"    ✓ Prompts: {len(prompts)} templates + {len(faqs)} FAQs")
    except Exception as e:
        print(f"    ⚠ Prompt export failed: {e}")


def create_backup(output_dir: str = None) -> str:
    timestamp   = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_name = f"kccitm_backup_{timestamp}"
    temp_dir    = Path(f"/tmp/{backup_name}")
    temp_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n\033[94mCreating backup: {backup_name}\033[0m")

    print("  [1/5] MySQL dump...")
    _mysql_dump(temp_dir)

    print("  [2/5] SQLite databases...")
    _copy_sqlite_dbs(temp_dir / "sqlite")

    print("  [3/5] Training data...")
    training_src = Path("data/training")
    if training_src.exists():
        shutil.copytree(training_src, temp_dir / "training", dirs_exist_ok=True)
        count = len(list(training_src.glob("*.jsonl")))
        print(f"    ✓ {count} JSONL files")
    else:
        print("    ○ No training data yet")

    print("  [4/5] Model metadata...")
    models_dir = Path("data/models")
    if models_dir.exists():
        meta_dest = temp_dir / "models"
        meta_dest.mkdir()
        for meta in models_dir.glob("*/training_meta.json"):
            shutil.copy2(meta, meta_dest / f"{meta.parent.name}_meta.json")
        print(f"    ✓ Model metadata copied")
    else:
        print("    ○ No model versions yet")

    print("  [5/5] Configuration...")
    config_dest = temp_dir / "config"
    config_dest.mkdir()
    env = Path(".env")
    if env.exists():
        shutil.copy2(env, config_dest / ".env")
    _export_prompts_json(config_dest)

    # Create archive
    dest_dir = Path(output_dir) if output_dir else BACKUP_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    archive = dest_dir / f"{backup_name}.tar.gz"

    with tarfile.open(archive, "w:gz") as tar:
        tar.add(temp_dir, arcname=backup_name)

    shutil.rmtree(temp_dir, ignore_errors=True)

    size_mb = archive.stat().st_size / 1_048_576
    print(f"\n\033[92m✓ Backup: {archive} ({size_mb:.1f} MB)\033[0m")
    return str(archive)


def restore_backup(archive_path: str) -> None:
    archive = Path(archive_path)
    if not archive.exists():
        print(f"\033[91mNot found: {archive_path}\033[0m")
        return

    print(f"\n\033[93m⚠ WARNING: This will overwrite current data!\033[0m")
    confirm = input("Type 'RESTORE' to confirm: ")
    if confirm.strip() != "RESTORE":
        print("Aborted.")
        return

    temp_dir = Path("/tmp/kccitm_restore")
    if temp_dir.exists():
        shutil.rmtree(temp_dir)

    print(f"\nExtracting {archive_path}...")
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(temp_dir)

    dirs = list(temp_dir.iterdir())
    root = dirs[0] if dirs else temp_dir

    # SQLite
    sqlite_dir = root / "sqlite"
    if sqlite_dir.exists():
        for db in sqlite_dir.glob("*.db"):
            dest = Path(f"data/{db.name}")
            shutil.copy2(db, dest)
            print(f"  ✓ Restored {db.name}")

    # MySQL
    dump = root / "mysql_dump.sql"
    if dump.exists():
        print("  Restoring MySQL...")
        try:
            from config import settings
            result = subprocess.run(
                ["mysql",
                 f"--host={settings.MYSQL_HOST}",
                 f"--port={settings.MYSQL_PORT}",
                 f"--user={settings.MYSQL_USER}",
                 f"--password={settings.MYSQL_PASSWORD}",
                 settings.MYSQL_DB],
                input=dump.read_text(), capture_output=True, text=True, timeout=120,
            )
            msg = "✓ MySQL restored" if result.returncode == 0 else f"⚠ {result.stderr[:80]}"
            print(f"  {msg}")
        except Exception as e:
            print(f"  ⚠ MySQL restore failed: {e}")

    # Training data
    training_src = root / "training"
    if training_src.exists():
        Path("data/training").mkdir(parents=True, exist_ok=True)
        shutil.copytree(training_src, Path("data/training"), dirs_exist_ok=True)
        print("  ✓ Training data restored")

    # .env
    env_src = root / "config" / ".env"
    if env_src.exists():
        shutil.copy2(env_src, ".env")
        print("  ✓ .env restored")

    shutil.rmtree(temp_dir, ignore_errors=True)
    print("\n\033[92m✓ Restore complete. Restart the server.\033[0m")


def list_backups() -> None:
    BACKUP_DIR.mkdir(exist_ok=True)
    backups = sorted(BACKUP_DIR.glob("*.tar.gz"), reverse=True)
    if not backups:
        print("No backups found in ./backups/")
        return
    print(f"\n{'Backup':<45} {'Size':>8}")
    print("-" * 55)
    for b in backups:
        size = b.stat().st_size / 1_048_576
        print(f"{b.name:<45} {size:>7.1f}M")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KCCITM Backup Tool")
    sub = parser.add_subparsers(dest="cmd")

    create_p = sub.add_parser("create")
    create_p.add_argument("--output", help="Output directory")

    restore_p = sub.add_parser("restore")
    restore_p.add_argument("path", help="Path to .tar.gz backup")

    sub.add_parser("list")

    args = parser.parse_args()
    if args.cmd == "create":
        create_backup(args.output)
    elif args.cmd == "restore":
        restore_backup(args.path)
    elif args.cmd == "list":
        list_backups()
    else:
        parser.print_help()

"""
Seed the initial admin user in sessions.db.

Run:
    python -m ingestion.create_admin

Change the password in production!
"""
import asyncio
import uuid

from api.middleware.auth import hash_password
from config import settings
from db.sqlite_client import execute, fetch_one, init_all_dbs


async def create_admin() -> None:
    init_all_dbs(settings)  # Ensure tables exist

    existing = await fetch_one(
        settings.SESSION_DB,
        "SELECT id FROM users WHERE role = 'admin'",
    )
    if existing:
        print("Admin user already exists. No action taken.")
        return

    user_id = str(uuid.uuid4())
    username = "admin"
    password = "admin123"  # Change in production!

    await execute(
        settings.SESSION_DB,
        "INSERT INTO users (id, username, password_hash, role) VALUES (?, ?, ?, ?)",
        (user_id, username, hash_password(password), "admin"),
    )

    print(f"\033[92m✓ Admin user created\033[0m")
    print(f"  Username : {username}")
    print(f"  Password : {password}")
    print(f"  \033[93m⚠ Change the password in production!\033[0m")


if __name__ == "__main__":
    asyncio.run(create_admin())

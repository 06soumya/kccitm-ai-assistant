"""
setup_users_table.py — One-time idempotent setup script.

Creates the `users` table in the kccitm MySQL database and seeds the initial
admin account with a bcrypt-hashed password.

Usage:
    python setup_users_table.py

Requires the following environment variables to be set in .env:
    ADMIN_USER   — username for the initial admin account
    ADMIN_PASS   — plaintext password (will be hashed; never stored as-is)

The script is safe to run multiple times:
    - CREATE TABLE IF NOT EXISTS prevents duplicate-table errors
    - INSERT IGNORE prevents duplicate-row errors if the admin account exists
"""

import os
import bcrypt
from dotenv import load_dotenv

from db_marks import get_connection


def main():
    load_dotenv()

    admin_user = os.getenv("ADMIN_USER")
    admin_pass = os.getenv("ADMIN_PASS")

    if not admin_user or not admin_pass:
        raise EnvironmentError(
            "ADMIN_USER and ADMIN_PASS must be set in .env before running this script"
        )

    # Hash the admin password — bcrypt.hashpw requires bytes input (not str)
    hashed = bcrypt.hashpw(admin_pass.encode("utf-8"), bcrypt.gensalt())
    password_hash = hashed.decode("utf-8")

    connection = get_connection()
    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            INT AUTO_INCREMENT PRIMARY KEY,
                username      VARCHAR(100) NOT NULL UNIQUE,
                password_hash VARCHAR(60)  NOT NULL,
                role          ENUM('admin', 'faculty') NOT NULL,
                is_active     BOOLEAN DEFAULT TRUE,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            "INSERT IGNORE INTO users (username, password_hash, role) VALUES (%s, %s, 'admin')",
            (admin_user, password_hash),
        )

        connection.commit()
        cursor.close()
    finally:
        connection.close()

    print(f"users table ready. Admin account '{admin_user}' seeded (or already exists).")


if __name__ == "__main__":
    main()

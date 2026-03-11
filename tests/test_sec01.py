"""
SEC-01 automated verification tests.

These tests verify:
1. DB credentials are loaded from environment variables (not hardcoded)
2. No plaintext password appears in source files

Tests test_credentials_from_env and test_db_connection_from_env will FAIL
until Plan 02 patches db_marks.py and db_connection.py to use os.environ.

test_no_plaintext_password will FAIL until Plan 02 removes the hardcoded
password from both files.
"""

import os
import pathlib
import importlib
import sys
from unittest.mock import patch, MagicMock


def test_credentials_from_env():
    """Assert db_marks.get_connection() uses env vars, not hardcoded credentials."""
    fake_env = {
        "DB_HOST": "test-host",
        "DB_USER": "test-user",
        "DB_PASSWORD": "test-secret",
        "DB_NAME": "test-db",
    }

    mock_connect = MagicMock()

    with patch.dict(os.environ, fake_env, clear=False):
        with patch("mysql.connector.connect", mock_connect):
            # Remove cached module so it reimports with fresh env
            if "db_marks" in sys.modules:
                del sys.modules["db_marks"]

            import db_marks
            db_marks.get_connection()

    # The password used must come from the env var, not a hardcoded string
    call_kwargs = mock_connect.call_args[1]
    assert call_kwargs.get("password") == "test-secret", (
        f"Expected password from DB_PASSWORD env var ('test-secret'), "
        f"got: {call_kwargs.get('password')!r}. "
        "db_marks.get_connection() must use os.environ.get('DB_PASSWORD') or "
        "load_dotenv() + os.getenv('DB_PASSWORD')."
    )


def test_db_connection_from_env():
    """Assert db_connection.get_connection() uses env vars, not hardcoded credentials."""
    fake_env = {
        "DB_HOST": "test-host",
        "DB_USER": "test-user",
        "DB_PASSWORD": "test-secret",
        "DB_NAME": "test-db",
    }

    mock_connect = MagicMock()

    with patch.dict(os.environ, fake_env, clear=False):
        with patch("mysql.connector.connect", mock_connect):
            if "db_connection" in sys.modules:
                del sys.modules["db_connection"]

            import db_connection
            db_connection.get_connection()

    call_kwargs = mock_connect.call_args[1]
    assert call_kwargs.get("password") == "test-secret", (
        f"Expected password from DB_PASSWORD env var ('test-secret'), "
        f"got: {call_kwargs.get('password')!r}. "
        "db_connection.get_connection() must use os.environ.get('DB_PASSWORD') or "
        "load_dotenv() + os.getenv('DB_PASSWORD')."
    )


def test_no_plaintext_password():
    """Assert the literal plaintext password does not appear in source files."""
    project_root = pathlib.Path(__file__).parent.parent
    plaintext_password = "qCsfeuECc3MW"

    db_marks_src = (project_root / "db_marks.py").read_text()
    assert plaintext_password not in db_marks_src, (
        "Plaintext password found in db_marks.py — "
        "must be removed and replaced with os.environ / .env loading."
    )

    db_connection_src = (project_root / "db_connection.py").read_text()
    assert plaintext_password not in db_connection_src, (
        "Plaintext password found in db_connection.py — "
        "must be removed and replaced with os.environ / .env loading."
    )

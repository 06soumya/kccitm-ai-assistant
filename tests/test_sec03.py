"""
SEC-03 automated verification tests.

These tests verify bcrypt hash format and round-trip behavior.

These tests do NOT require a live database — they test the bcrypt library
in isolation. Both tests should pass immediately after bcrypt is installed.
"""

import bcrypt


def test_bcrypt_hash_format():
    """Assert bcrypt produces a hash starting with $2b$ of length 60."""
    password = b"test_password"
    hashed = bcrypt.hashpw(password, bcrypt.gensalt())

    assert hashed.startswith(b"$2b$"), (
        f"Expected bcrypt hash to start with b'$2b$', got: {hashed[:10]!r}"
    )
    assert len(hashed) == 60, (
        f"Expected bcrypt hash length 60, got: {len(hashed)}"
    )


def test_bcrypt_round_trip():
    """Assert bcrypt checkpw returns True for correct password and False for wrong."""
    password = b"test_password"
    hashed = bcrypt.hashpw(password, bcrypt.gensalt())

    assert bcrypt.checkpw(b"test_password", hashed), (
        "bcrypt.checkpw returned False for correct password — round-trip verification failed."
    )
    assert not bcrypt.checkpw(b"wrong_password", hashed), (
        "bcrypt.checkpw returned True for wrong password — this should never happen."
    )

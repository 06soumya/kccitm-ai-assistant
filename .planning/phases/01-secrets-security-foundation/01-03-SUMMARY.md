---
plan: 01-03
phase: 01-secrets-security-foundation
status: complete
---

## What Was Built

`setup_users_table.py` — idempotent one-time script that creates the `users` table in the `kccitm` MySQL database and seeds the initial admin account with a bcrypt-hashed password.

## Artifacts

- `setup_users_table.py` — at project root; reads `ADMIN_USER`/`ADMIN_PASS` from `.env`, hashes with bcrypt, creates table, inserts admin via `INSERT IGNORE`

## Verification

```
pytest tests/test_sec03.py -v
# 2 passed: test_bcrypt_hash_format, test_bcrypt_round_trip

pytest tests/ -m "not integration" -q
# 392 passed, 0 failed

python setup_users_table.py
# users table ready. Admin account 'kccsw' seeded (or already exists).

python setup_users_table.py  # second run — idempotent
# users table ready. Admin account 'kccsw' seeded (or already exists).
```

DB verification (via Python):
- Schema: id, username, password_hash (VARCHAR 60), role ENUM, is_active, created_at ✓
- password_hash starts with `$2b$12$` (bcrypt format, not plaintext) ✓

## Additional Fix

Fixed test isolation bug in `tests/test_sec01.py`: `test_credentials_from_env` and `test_db_connection_from_env` were deleting and reimporting `db_marks`/`db_connection` from `sys.modules` without restoring the originals. This caused `patch("db_marks.get_available_batch_years")` in `test_topper_and_clarification.py` to patch the wrong module object, making 4 tests fail when run in the full suite. Fixed by saving and restoring the original module in a `try/finally` block.

## Requirements Met

- SEC-03: passwords stored with bcrypt hashing in MySQL users table ✓
- Script guard prevents empty-password seeding ✓
- `INSERT IGNORE` makes script safe to re-run ✓
- bcrypt `.encode("utf-8")` used correctly (no str→bytes TypeError) ✓
- No plaintext password anywhere in DB or source ✓

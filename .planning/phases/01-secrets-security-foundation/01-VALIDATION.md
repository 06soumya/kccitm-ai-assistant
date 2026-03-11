---
phase: 1
slug: secrets-security-foundation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-11
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (none detected — Wave 0 installs) |
| **Config file** | none — Wave 0 creates tests/ directory |
| **Quick run command** | `pytest tests/ -x -q -m "not integration"` |
| **Full suite command** | `pytest tests/ -v -m "not integration"` |
| **Estimated runtime** | ~3 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/ -x -q -m "not integration"`
- **After every plan wave:** Run `pytest tests/ -v -m "not integration"`
- **Before `/gsd:verify-work`:** Full suite must be green + manual `users` table schema check
- **Max feedback latency:** ~3 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 1-01-01 | 01 | 0 | SEC-01, SEC-02, SEC-03 | setup | `pytest tests/ -x -q -m "not integration"` | ❌ W0 | ⬜ pending |
| 1-02-01 | 02 | 1 | SEC-02 | smoke | `pytest tests/test_sec02.py -x -q` | ❌ W0 | ⬜ pending |
| 1-02-02 | 02 | 1 | SEC-01 | unit | `pytest tests/test_sec01.py -x -q` | ❌ W0 | ⬜ pending |
| 1-02-03 | 02 | 1 | SEC-01 | smoke | `pytest tests/test_sec01.py::test_no_plaintext_password -x` | ❌ W0 | ⬜ pending |
| 1-03-01 | 03 | 1 | SEC-03 | unit | `pytest tests/test_sec03.py -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/__init__.py` — empty package marker
- [ ] `tests/test_sec01.py` — SEC-01: env-loading pattern, no plaintext credential check
- [ ] `tests/test_sec02.py` — SEC-02: .gitignore entries for `.env` and `chat_memory.json`
- [ ] `tests/test_sec03.py` — SEC-03: bcrypt hash format and round-trip verification
- [ ] `pytest` added to `requirements.txt`

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| `users` table exists with correct schema in MySQL | SEC-03 | Requires live MySQL instance | Run `python setup_users_table.py`, then `mysql -u root -p -e "DESCRIBE kccitm.users;"` and verify columns: id, username, password_hash, role, is_active, created_at |
| App starts successfully reading DB credentials from `.env` | SEC-01 | Requires live MySQL + Streamlit | Run `streamlit run app.py` and confirm no credential errors on startup |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending

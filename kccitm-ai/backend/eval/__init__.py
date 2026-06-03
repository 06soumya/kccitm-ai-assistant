"""
KCCITM AI Assistant — evaluation harness.

Loads a fixed set of test queries from queries.yaml, runs each through the
orchestrator, and reports per-query pass/fail. Used to detect regressions
and quantify accuracy across releases.
"""

"""
Regression test suite — validates entire system after any code change.

Usage:
  python -m tools.regression              # Run all tests
  python -m tools.regression --fast       # Skip slow LLM tests
  python -m tools.regression --layer api  # Test specific layer
  python -m tools.regression --report     # Generate HTML report
"""
import asyncio
import argparse
import time
import httpx
from dataclasses import dataclass, field
from pathlib import Path

API_BASE = "http://localhost:8000"
_TOKEN_CACHE: dict = {}


@dataclass
class TestResult:
    name: str
    layer: str
    passed: bool
    duration_ms: float
    error: str = ""
    details: str = ""


@dataclass
class RegressionReport:
    results: list = field(default_factory=list)
    start_time: float = 0
    end_time: float = 0

    @property
    def total(self):   return len(self.results)
    @property
    def passed(self):  return sum(1 for r in self.results if r.passed)
    @property
    def failed(self):  return sum(1 for r in self.results if not r.passed)
    @property
    def duration_s(self): return self.end_time - self.start_time

    def add(self, name, layer, passed, duration_ms, error="", details=""):
        self.results.append(TestResult(name, layer, passed, duration_ms, error, details))

    def print_summary(self):
        print(f"\n{'='*60}")
        print("REGRESSION TEST REPORT")
        print(f"{'='*60}")
        print(f"Duration: {self.duration_s:.1f}s | Total: {self.total} | Passed: {self.passed} | Failed: {self.failed}")
        print()

        layers: dict = {}
        for r in self.results:
            layers.setdefault(r.layer, []).append(r)

        for layer, tests in layers.items():
            lp = sum(1 for t in tests if t.passed)
            print(f"  [{layer}] {lp}/{len(tests)}")
            for t in tests:
                icon = "\033[92m✓\033[0m" if t.passed else "\033[91m✗\033[0m"
                print(f"    {icon} {t.name} ({t.duration_ms:.0f}ms)"
                      + (f"\n      {t.error[:80]}" if not t.passed and t.error else ""))

        pct = self.passed / self.total * 100 if self.total > 0 else 0
        col = "\033[92m" if pct >= 90 else "\033[93m" if pct >= 70 else "\033[91m"
        print(f"\n{col}Overall: {pct:.0f}% pass rate\033[0m")

    def save_html(self, path="data/regression_report.html"):
        rows = ""
        for r in self.results:
            s, c = ("✓", "#22c55e") if r.passed else ("✗", "#ef4444")
            rows += (f"<tr><td style='color:{c}'>{s}</td><td>{r.layer}</td>"
                     f"<td>{r.name}</td><td>{r.duration_ms:.0f}ms</td><td>{r.error}</td></tr>\n")
        pct = f"{self.passed/self.total*100:.0f}%" if self.total else "N/A"
        html = (f"<html><head><title>KCCITM Regression Report</title>"
                f"<style>body{{font-family:system-ui;padding:2rem}}"
                f"table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #e5e7eb;padding:8px;text-align:left}}"
                f"th{{background:#f3f4f6}}</style></head>"
                f"<body><h1>Regression Report</h1><p>Passed: {self.passed}/{self.total} ({pct})</p>"
                f"<table><tr><th>Status</th><th>Layer</th><th>Test</th><th>Time</th><th>Error</th></tr>"
                f"{rows}</table></body></html>")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(html)
        print(f"Report saved to {path}")


async def _run_test(report: RegressionReport, name: str, layer: str, fn):
    start = time.time()
    try:
        result = await fn()
        duration = (time.time() - start) * 1000
        passed, details = result if isinstance(result, tuple) else (bool(result), "")
        report.add(name, layer, passed, duration, details=str(details))
    except Exception as e:
        duration = (time.time() - start) * 1000
        report.add(name, layer, False, duration, error=str(e)[:200])


async def _get_token():
    if "token" not in _TOKEN_CACHE:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{API_BASE}/api/auth/login",
                             json={"username": "admin", "password": "admin123"})
            _TOKEN_CACHE["token"] = r.json()["access_token"]
    return _TOKEN_CACHE["token"]


async def _authed_get(path):
    t = await _get_token()
    async with httpx.AsyncClient(timeout=120) as c:
        return await c.get(f"{API_BASE}{path}", headers={"Authorization": f"Bearer {t}"})


async def _authed_post(path, data=None):
    t = await _get_token()
    async with httpx.AsyncClient(timeout=120) as c:
        return await c.post(f"{API_BASE}{path}",
                            headers={"Authorization": f"Bearer {t}", "Content-Type": "application/json"},
                            json=data or {})


# ── Individual tests ──────────────────────────────────────────────
async def t_health():
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{API_BASE}/api/health")
        d = r.json()
        ok = all(s.get("status") == "ok" for s in d.get("services", {}).values())
        return ok, d.get("status")

async def t_login_valid():
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{API_BASE}/api/auth/login",
                         json={"username": "admin", "password": "admin123"})
        return r.status_code == 200, f"HTTP {r.status_code}"

async def t_login_invalid():
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{API_BASE}/api/auth/login",
                         json={"username": "admin", "password": "wrongpassword"})
        return r.status_code == 401, f"HTTP {r.status_code} (expected 401)"

async def t_unauthorized():
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{API_BASE}/api/sessions")
        return r.status_code in (401, 403), f"HTTP {r.status_code}"

async def t_session_crud():
    r = await _authed_post("/api/sessions")
    sid = r.json().get("session_id")
    if not sid:
        return False, "No session_id returned"
    r2 = await _authed_get(f"/api/sessions/{sid}")
    if r2.status_code != 200:
        return False, f"GET failed: HTTP {r2.status_code}"
    t = await _get_token()
    async with httpx.AsyncClient(timeout=15) as c:
        rd = await c.delete(f"{API_BASE}/api/sessions/{sid}",
                            headers={"Authorization": f"Bearer {t}"})
    return rd.status_code == 200, "CRUD OK"

async def t_sessions_list():
    r = await _authed_get("/api/sessions")
    return r.status_code == 200, f"HTTP {r.status_code}"

async def t_cache_stats():
    r = await _authed_get("/api/admin/cache/stats")
    return r.status_code == 200, f"entries={r.json().get('total_entries')}"

async def t_quality_stats():
    r = await _authed_get("/api/admin/dashboard/quality")
    return r.status_code == 200, f"avg={r.json().get('avg_score')}"

async def t_dashboard_metrics():
    r = await _authed_get("/api/admin/dashboard/metrics")
    return r.status_code == 200, f"sessions={r.json().get('sessions')}"

async def t_faqs_endpoint():
    r = await _authed_get("/api/admin/faqs")
    return r.status_code == 200, f"faqs={len(r.json().get('faqs',[]))}"

async def t_prompts_endpoint():
    r = await _authed_get("/api/admin/prompts")
    return r.status_code == 200, f"prompts={len(r.json().get('prompts',[]))}"

async def t_models_endpoint():
    r = await _authed_get("/api/admin/models")
    return r.status_code == 200, f"active={r.json().get('active')}"

async def t_training_stats():
    r = await _authed_get("/api/admin/training/stats")
    d = r.json()
    total = d.get("total_candidates", d.get("total", 0))
    return r.status_code == 200, f"candidates={total}"

async def t_chunks_health():
    r = await _authed_get("/api/admin/chunks/health")
    d = r.json()
    n = len(d.get("chunks", d if isinstance(d, list) else []))
    return r.status_code == 200, f"tracked={n}"

async def t_healing_queue():
    r = await _authed_get("/api/admin/dashboard/healing")
    return r.status_code == 200, f"HTTP {r.status_code}"

async def t_chat_sql():
    r = await _authed_post("/api/chat",
                           {"message": "top 3 students by SGPA in semester 1", "stream": False})
    d = r.json()
    ok = r.status_code == 200 and len(d.get("response", "")) > 10
    return ok, f"route={d.get('route_used')} len={len(d.get('response',''))}"

async def t_chat_rag():
    r = await _authed_post("/api/chat",
                           {"message": "tell me about CSE batch performance", "stream": False})
    d = r.json()
    ok = r.status_code == 200 and len(d.get("response", "")) > 10
    return ok, f"route={d.get('route_used')}"

async def t_chat_keyword():
    r = await _authed_post("/api/chat",
                           {"message": "KCS503 results", "stream": False})
    d = r.json()
    return r.status_code == 200 and len(d.get("response", "")) > 5, f"route={d.get('route_used')}"

async def t_feedback_submit():
    # Create session + send message first
    rs = await _authed_post("/api/sessions")
    sid = rs.json().get("session_id", "")
    rc = await _authed_post("/api/chat", {"message": "how many students are there", "session_id": sid, "stream": False})
    msg_id = rc.json().get("message_id", "test-id")
    rf = await _authed_post("/api/feedback",
                            {"message_id": msg_id, "session_id": sid, "rating": 5})
    return rf.status_code == 200, f"HTTP {rf.status_code}"


async def run_all(fast: bool = False, layer: str = None):
    report = RegressionReport()
    report.start_time = time.time()

    ALL = [
        ("Health check",              "infra",    t_health),
        ("Login valid",               "auth",     t_login_valid),
        ("Login invalid rejected",    "auth",     t_login_invalid),
        ("Unauthenticated blocked",   "auth",     t_unauthorized),
        ("Session CRUD",              "sessions", t_session_crud),
        ("Sessions list",             "sessions", t_sessions_list),
        ("Cache stats",               "admin",    t_cache_stats),
        ("Dashboard metrics",         "admin",    t_dashboard_metrics),
        ("Quality stats",             "admin",    t_quality_stats),
        ("FAQs endpoint",             "admin",    t_faqs_endpoint),
        ("Prompts endpoint",          "admin",    t_prompts_endpoint),
        ("Models endpoint",           "admin",    t_models_endpoint),
        ("Training stats",            "admin",    t_training_stats),
        ("Chunks health",             "admin",    t_chunks_health),
        ("Healing queue",             "admin",    t_healing_queue),
    ]

    if not fast:
        ALL += [
            ("Chat SQL route",        "chat",     t_chat_sql),
            ("Chat RAG route",        "chat",     t_chat_rag),
            ("Chat keyword route",    "chat",     t_chat_keyword),
            ("Feedback submit",       "chat",     t_feedback_submit),
        ]

    if layer:
        ALL = [(n, l, f) for n, l, f in ALL if l == layer]

    print(f"\nRunning {len(ALL)} regression tests" + (" (fast mode)" if fast else "") + "...\n")
    for name, test_layer, fn in ALL:
        await _run_test(report, name, test_layer, fn)

    report.end_time = time.time()
    report.print_summary()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KCCITM Regression Tests")
    parser.add_argument("--fast",   action="store_true", help="Skip slow LLM tests")
    parser.add_argument("--layer",  choices=["infra", "auth", "sessions", "chat", "admin"])
    parser.add_argument("--report", action="store_true", help="Generate HTML report")
    args = parser.parse_args()
    report = asyncio.run(run_all(args.fast, args.layer))
    if args.report:
        report.save_html()

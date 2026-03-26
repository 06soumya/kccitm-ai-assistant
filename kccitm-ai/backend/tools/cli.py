"""
Full CLI admin tool for KCCITM AI Assistant.

Usage:
  python -m tools.cli                    # Interactive menu
  python -m tools.cli status             # Quick system status
  python -m tools.cli chat               # Terminal chat client
  python -m tools.cli cache stats        # Cache statistics
  python -m tools.cli cache clear        # Clear cache
  python -m tools.cli sessions list      # List sessions
  python -m tools.cli feedback stats     # Quality stats
  python -m tools.cli healing list       # View healing queue
  python -m tools.cli healing approve <id>
  python -m tools.cli faqs list
  python -m tools.cli prompts list
  python -m tools.cli training stats
  python -m tools.cli training export
  python -m tools.cli models list
  python -m tools.cli models switch <name>
  python -m tools.cli jobs run healing|faq|prompts
  python -m tools.cli users list
  python -m tools.cli users create <username> <password> <role>
"""
import asyncio
import sys
import time
import httpx

API_BASE = "http://localhost:8000"
_TOKEN = None


def _headers():
    return {"Authorization": f"Bearer {_TOKEN}", "Content-Type": "application/json"}


async def _login():
    global _TOKEN
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{API_BASE}/api/auth/login",
                         json={"username": "admin", "password": "admin123"})
        if r.status_code == 200:
            _TOKEN = r.json()["access_token"]
            return True
        print(f"\033[91mLogin failed: {r.text}\033[0m")
        return False


async def _get(path):
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.get(f"{API_BASE}{path}", headers=_headers())
        return r.json() if r.status_code == 200 else {"error": r.text}


async def _post(path, data=None):
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{API_BASE}{path}", headers=_headers(), json=data or {})
        return r.json() if r.status_code == 200 else {"error": r.text}


async def _delete(path):
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.delete(f"{API_BASE}{path}", headers=_headers())
        return r.json() if r.status_code == 200 else {"error": r.text}


# ──────────────────────────────────────────────────────────────────
async def cmd_status():
    health   = await _get("/api/health")
    metrics  = await _get("/api/admin/dashboard/metrics")
    cache    = await _get("/api/admin/cache/stats")
    quality  = await _get("/api/admin/dashboard/quality")

    print(f"\n\033[94m{'='*50}\033[0m")
    print(f"\033[94mKCCITM AI Assistant — System Status\033[0m")
    print(f"\033[94m{'='*50}\033[0m")

    for svc, info in health.get("services", {}).items():
        status = info.get("status", "?")
        dot = "\033[92m●\033[0m" if status == "ok" else "\033[91m●\033[0m"
        print(f"  {dot} {svc}: {status}")

    print(f"\n  Users: {metrics.get('users', 0)} | Sessions: {metrics.get('sessions', 0)} | Messages: {metrics.get('messages', 0)}")

    avg = quality.get("avg_score", 0) or 0
    col = "\033[92m" if avg >= 0.7 else "\033[93m" if avg >= 0.5 else "\033[91m"
    print(f"  Avg Quality: {col}{avg:.3f}\033[0m")
    dist = quality.get("distribution", {})
    print(f"  Distribution: ✓{dist.get('good',0)} ~{dist.get('acceptable',0)} ⚠{dist.get('poor',0)} ✗{dist.get('failed',0)}")
    print(f"  Cache: {cache.get('active_entries', 0)} entries, {cache.get('total_hits', 0)} hits\n")


async def cmd_chat():
    session = await _post("/api/sessions")
    sid = session.get("session_id", "")
    print(f"\n\033[94mKCCITM AI Chat (session {sid[:8]}...)\033[0m")
    print("Type 'quit' to exit.\n")

    while True:
        try:
            query = input("\033[96mYou: \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not query or query.lower() in ("quit", "exit", "q"):
            break

        start = time.time()
        result = await _post("/api/chat", {"message": query, "session_id": sid, "stream": False})
        elapsed = (time.time() - start) * 1000

        if "error" in result:
            print(f"\033[91mError: {result['error']}\033[0m")
        else:
            route = result.get("route_used", "?")
            print(f"\n\033[93m[{route} | {elapsed:.0f}ms]\033[0m")
            print(f"\033[97m{result.get('response', '—')}\033[0m\n")


async def cmd_cache(sub):
    if sub == "stats":
        d = await _get("/api/admin/cache/stats")
        print(f"\nCache: {d.get('active_entries',0)} active / {d.get('total_entries',0)} total | Hits: {d.get('total_hits',0)}")
        for q in d.get("top_queries", [])[:5]:
            print(f"  [{q.get('hit_count',0)} hits] {q.get('query_text','')[:60]}")
    elif sub == "clear":
        r = await _post("/api/admin/cache/clear")
        print(f"\033[92m✓ {r.get('message','Cache cleared')}\033[0m")


async def cmd_sessions(sub):
    if sub == "list":
        d = await _get("/api/sessions")
        for s in d.get("sessions", []):
            print(f"  {s['id'][:8]}... | {s.get('title','(untitled)')[:40]} | {s.get('message_count',0)} msgs | {s.get('updated_at','')[:10]}")


async def cmd_feedback(sub):
    if sub == "stats":
        d = await _get("/api/admin/dashboard/quality")
        print(f"\nQuality: avg={d.get('avg_score',0):.3f}, total={d.get('total',0)}")
        dist = d.get("distribution", {})
        print(f"  Good (≥0.7):  {dist.get('good',0)}")
        print(f"  Acceptable:   {dist.get('acceptable',0)}")
        print(f"  Poor:         {dist.get('poor',0)}")
        print(f"  Failed (<0.3):{dist.get('failed',0)}")


async def cmd_healing(sub, fix_id=None):
    if sub == "list":
        d = await _get("/api/admin/dashboard/healing")
        queue = d.get("queue", [])
        print(f"\nHealing queue: {len(queue)} pending")
        for fix in queue:
            print(f"  {str(fix.get('id',''))[:8]} | {fix.get('failure_category','?')} | {str(fix.get('query',''))[:50]}")
    elif sub == "approve" and fix_id:
        r = await _post(f"/api/admin/dashboard/healing/{fix_id}/approve")
        print(f"\033[92m✓ {r.get('message','Approved')}\033[0m")
    elif sub == "reject" and fix_id:
        r = await _post(f"/api/admin/dashboard/healing/{fix_id}/reject")
        print(f"\033[92m✓ {r.get('message','Rejected')}\033[0m")


async def cmd_faqs(sub):
    if sub == "list":
        d = await _get("/api/admin/faqs")
        for f in d.get("faqs", []):
            v = "✓" if f.get("admin_verified") else " "
            print(f"  [{v}] hits={f.get('hit_count',0)} | {f.get('canonical_question', f.get('question',''))[:60]}")


async def cmd_prompts(sub):
    if sub == "list":
        d = await _get("/api/admin/prompts")
        for p in d.get("prompts", []):
            print(f"  {p['prompt_name']}/{p.get('section_name','default')} v{p.get('version',1)} | queries={p.get('query_count',0)}")
    elif sub == "proposals":
        d = await _get("/api/admin/prompts/proposals")
        for p in d.get("proposals", []):
            print(f"  {str(p.get('id',''))[:8]} | {p.get('target_prompt','?')}/{p.get('target_section','?')} | {p.get('reasoning','')[:50]}")


async def cmd_training(sub):
    if sub == "stats":
        d = await _get("/api/admin/training/stats")
        total = d.get("total_candidates", d.get("total", 0))
        print(f"\nTraining pool: {total} candidates")
        print(f"Ready for LoRA: {'✓' if total >= 500 else f'✗ (need {500-total} more)'}")
        for cat, cnt in d.get("by_category", {}).items():
            print(f"  {cat}: {cnt}")
    elif sub == "export":
        r = await _post("/api/admin/training/export")
        print(f"Exported: {r.get('total',0)} entries | run_id: {r.get('run_id','?')}")
        for f, c in r.get("files", {}).items():
            print(f"  {f}: {c} entries")


async def cmd_models(sub, name=None):
    if sub == "list":
        d = await _get("/api/admin/models")
        active = d.get("active", "?")
        for m in d.get("models", []):
            flag = " ← ACTIVE" if m.get("is_active") or m.get("model_name") == active else ""
            print(f"  {m.get('model_name','?')} ({m.get('type','?')}){flag}")
    elif sub == "switch" and name:
        r = await _post("/api/admin/models/switch", {"model_name": name})
        print(f"{r.get('message', 'Done — restart server to apply.')}")


async def cmd_jobs(job):
    endpoints = {
        "healing": "/api/admin/jobs/healing/run",
        "faq":     "/api/admin/jobs/faq/run",
        "prompts": "/api/admin/jobs/prompts/run",
    }
    if job not in endpoints:
        print(f"Unknown job: {job}. Choose: {', '.join(endpoints)}")
        return
    print(f"Running {job} job...")
    r = await _post(endpoints[job])
    print(f"\033[92m✓ {r.get('message', str(r))}\033[0m")


async def cmd_users(sub, *args):
    if sub == "list":
        d = await _get("/api/auth/users")
        for u in d.get("users", []):
            print(f"  {u['username']} ({u['role']}) — {u.get('created_at','')[:10]}")
    elif sub == "create" and len(args) >= 3:
        r = await _post("/api/auth/register", {"username": args[0], "password": args[1], "role": args[2]})
        print(f"\033[92m✓ {r.get('message', 'User created')}\033[0m")


async def cmd_interactive():
    menu = [
        ("1", "System Status",   cmd_status),
        ("2", "Terminal Chat",   cmd_chat),
        ("3", "Cache Stats",     lambda: cmd_cache("stats")),
        ("4", "Quality Stats",   lambda: cmd_feedback("stats")),
        ("5", "Healing Queue",   lambda: cmd_healing("list")),
        ("6", "FAQ List",        lambda: cmd_faqs("list")),
        ("7", "Prompts",         lambda: cmd_prompts("list")),
        ("8", "Training Stats",  lambda: cmd_training("stats")),
        ("9", "Models",          lambda: cmd_models("list")),
        ("0", "Clear Cache",     lambda: cmd_cache("clear")),
    ]
    print(f"\n\033[94mKCCITM Admin CLI\033[0m")
    for k, label, _ in menu:
        print(f"  [{k}] {label}")
    print("  [q] Quit\n")

    lookup = {k: fn for k, _, fn in menu}
    while True:
        try:
            choice = input("\033[96m> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if choice in ("q", "quit"):
            break
        if choice in lookup:
            await lookup[choice]()
            print()


async def main():
    args = sys.argv[1:]
    if not await _login():
        return

    if not args:
        await cmd_interactive()
    elif args[0] == "status":
        await cmd_status()
    elif args[0] == "chat":
        await cmd_chat()
    elif args[0] == "cache":
        await cmd_cache(args[1] if len(args) > 1 else "stats")
    elif args[0] == "sessions":
        await cmd_sessions(args[1] if len(args) > 1 else "list")
    elif args[0] == "feedback":
        await cmd_feedback(args[1] if len(args) > 1 else "stats")
    elif args[0] == "healing":
        await cmd_healing(args[1] if len(args) > 1 else "list",
                          args[2] if len(args) > 2 else None)
    elif args[0] == "faqs":
        await cmd_faqs(args[1] if len(args) > 1 else "list")
    elif args[0] == "prompts":
        await cmd_prompts(args[1] if len(args) > 1 else "list")
    elif args[0] == "training":
        await cmd_training(args[1] if len(args) > 1 else "stats")
    elif args[0] == "models":
        await cmd_models(args[1] if len(args) > 1 else "list",
                         args[2] if len(args) > 2 else None)
    elif args[0] == "jobs" and len(args) > 2:
        await cmd_jobs(args[2])
    elif args[0] == "users":
        await cmd_users(args[1] if len(args) > 1 else "list", *args[2:])
    else:
        print(f"Unknown command: {' '.join(args)}\nRun without args for interactive menu.")


if __name__ == "__main__":
    asyncio.run(main())

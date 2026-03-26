"""
Per-query performance profiler — find bottlenecks in the pipeline.

Runs a set of queries and measures time spent in each stage:
  intent detection → cache lookup → routing → SQL/RAG generation → response

Usage:
  python -m tools.profiler                     # Profile default query set
  python -m tools.profiler --query "top 5..."  # Profile a single query
  python -m tools.profiler --count 20          # Profile N random queries
  python -m tools.profiler --report            # Save JSON report
"""
import asyncio
import argparse
import time
import json
import statistics
import random
import httpx
from dataclasses import dataclass, field
from pathlib import Path

API_BASE = "http://localhost:8000"

DEFAULT_QUERIES = [
    "top 5 students by SGPA in semester 1",
    "how many CSE students are there",
    "average SGPA in semester 4",
    "tell me about CSE batch performance",
    "which students are struggling in maths",
    "KCS503 results",
    "compare pass rates semester 1 vs semester 6",
    "analyze batch performance across all semesters",
    "students with back papers in semester 5",
    "who improved the most from semester 1 to 4",
]


@dataclass
class QueryProfile:
    query: str
    total_ms: float
    route: str
    cache_hit: bool
    response_len: int
    error: str = ""
    stages: dict = field(default_factory=dict)


@dataclass
class ProfileReport:
    profiles: list = field(default_factory=list)

    def add(self, p: QueryProfile):
        self.profiles.append(p)

    def print_summary(self):
        ok   = [p for p in self.profiles if not p.error]
        errs = [p for p in self.profiles if p.error]

        print(f"\n{'='*65}")
        print("QUERY PROFILER REPORT")
        print(f"{'='*65}")
        print(f"Queries profiled: {len(self.profiles)} | Successful: {len(ok)} | Errors: {len(errs)}")

        if ok:
            times = [p.total_ms for p in ok]
            print(f"\nLatency (ms):")
            print(f"  Min:    {min(times):.0f}")
            print(f"  Max:    {max(times):.0f}")
            print(f"  Mean:   {statistics.mean(times):.0f}")
            print(f"  Median: {statistics.median(times):.0f}")
            if len(times) >= 5:
                s = sorted(times)
                print(f"  P80:    {s[int(len(s)*0.8)]:.0f}")
                print(f"  P95:    {s[int(len(s)*0.95)]:.0f}")

            # Per-route breakdown
            routes: dict[str, list] = {}
            for p in ok:
                routes.setdefault(p.route, []).append(p.total_ms)
            print(f"\nBy route:")
            for route, ts in sorted(routes.items(), key=lambda x: -len(x[1])):
                print(f"  {route:<12} n={len(ts):>3}  mean={statistics.mean(ts):.0f}ms  max={max(ts):.0f}ms")

            # Cache hits
            hits = sum(1 for p in ok if p.cache_hit)
            print(f"\nCache: {hits}/{len(ok)} hits ({hits/len(ok)*100:.0f}%)")

        # Slowest queries
        slow = sorted(ok, key=lambda p: -p.total_ms)[:5]
        if slow:
            print(f"\nSlowest queries:")
            for p in slow:
                print(f"  {p.total_ms:>6.0f}ms  [{p.route}]  {p.query[:55]}")

        # Per-query detail
        print(f"\n{'Query':<50} {'Route':<10} {'ms':>6} {'Cache'}")
        print("-" * 75)
        for p in self.profiles:
            cache = "HIT" if p.cache_hit else "   "
            err   = f"  ERROR: {p.error[:30]}" if p.error else ""
            print(f"  {p.query[:48]:<50} {p.route:<10} {p.total_ms:>6.0f} {cache}{err}")

        print(f"{'='*65}\n")

    def save_json(self, path: str = "data/profile_report.json"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        data = [
            {
                "query":        p.query,
                "route":        p.route,
                "total_ms":     round(p.total_ms, 1),
                "cache_hit":    p.cache_hit,
                "response_len": p.response_len,
                "error":        p.error,
            }
            for p in self.profiles
        ]
        Path(path).write_text(json.dumps(data, indent=2))
        print(f"Report saved to {path}")


async def profile_query(client: httpx.AsyncClient, token: str, session_id: str,
                        query: str) -> QueryProfile:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    start = time.time()

    try:
        resp = await client.post(
            f"{API_BASE}/api/chat",
            headers=headers,
            json={"message": query, "session_id": session_id, "stream": False},
        )
        total_ms = (time.time() - start) * 1000

        if resp.status_code != 200:
            return QueryProfile(query=query, total_ms=total_ms, route="?",
                                cache_hit=False, response_len=0,
                                error=f"HTTP {resp.status_code}")

        d = resp.json()
        stages = {
            "total_time_ms":     d.get("total_time_ms", total_ms),
            "routing_time_ms":   d.get("routing_time_ms", 0),
            "generation_time_ms":d.get("generation_time_ms", 0),
        }
        return QueryProfile(
            query=query,
            total_ms=d.get("total_time_ms", total_ms),
            route=d.get("route_used", "?"),
            cache_hit=d.get("cache_hit", False),
            response_len=len(d.get("response", "")),
            stages=stages,
        )
    except Exception as e:
        total_ms = (time.time() - start) * 1000
        return QueryProfile(query=query, total_ms=total_ms, route="?",
                            cache_hit=False, response_len=0, error=str(e)[:80])


async def run_profiler(queries: list, save_report: bool = False) -> ProfileReport:
    print(f"\n\033[94mKCCITM Query Profiler\033[0m")
    print(f"Profiling {len(queries)} queries...\n")

    # Login
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{API_BASE}/api/auth/login",
                         json={"username": "admin", "password": "admin123"})
        if r.status_code != 200:
            print(f"\033[91mLogin failed\033[0m")
            return ProfileReport()
        token = r.json()["access_token"]

    # Create session
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{API_BASE}/api/sessions",
                         headers={"Authorization": f"Bearer {token}"})
        sid = r.json().get("session_id", "")

    report = ProfileReport()
    async with httpx.AsyncClient(timeout=180) as client:
        for i, q in enumerate(queries, 1):
            print(f"  [{i:>2}/{len(queries)}] {q[:55]}...", end="", flush=True)
            profile = await profile_query(client, token, sid, q)
            report.add(profile)
            status = f"\033[91m ERROR\033[0m" if profile.error else f"\033[92m {profile.total_ms:.0f}ms [{profile.route}]\033[0m"
            print(status)

    report.print_summary()
    if save_report:
        report.save_json()

    # Cleanup session
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            await c.delete(f"{API_BASE}/api/sessions/{sid}",
                           headers={"Authorization": f"Bearer {token}"})
        except Exception:
            pass

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KCCITM Query Profiler")
    parser.add_argument("--query",  help="Single query to profile")
    parser.add_argument("--count",  type=int, default=0, help="Number of random queries")
    parser.add_argument("--report", action="store_true", help="Save JSON report")
    args = parser.parse_args()

    if args.query:
        queries = [args.query]
    elif args.count > 0:
        queries = random.sample(DEFAULT_QUERIES * 3, min(args.count, len(DEFAULT_QUERIES) * 3))
    else:
        queries = DEFAULT_QUERIES

    asyncio.run(run_profiler(queries, args.report))

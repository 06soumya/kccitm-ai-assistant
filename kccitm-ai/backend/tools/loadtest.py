"""
Load test: simulate N concurrent users querying the API.
Measures: throughput, latency distribution, error rate, queue saturation.

Usage:
  python -m tools.loadtest                           # Default: 10 users, 5 queries each
  python -m tools.loadtest --users 30 --queries 10   # 30 users, 10 queries each
  python -m tools.loadtest --streaming               # Test SSE streaming
"""
import asyncio
import argparse
import time
import json
import statistics
import random
import httpx
from dataclasses import dataclass, field

API_BASE = "http://localhost:8000"

QUERY_POOL = [
    # SQL (40%)
    "top 5 students by SGPA in semester 1",
    "how many CSE students are there",
    "average SGPA in semester 4",
    "students with SGPA below 6 in semester 3",
    "compare pass rates between semester 1 and semester 6",
    "count students with grade C in KCS503",
    "list all students with back papers in semester 5",
    # RAG (40%)
    "tell me about the CSE batch performance",
    "which students are struggling in programming subjects",
    "describe the performance trend of CSE branch",
    "students who improved from semester 1 to semester 4",
    "who performed well in practicals",
    "KCS503 results",
    "KAS101T grades",
    # HYBRID (20%)
    "why did the average SGPA drop in semester 6",
    "analyze CSE batch performance across all semesters",
]


@dataclass
class LoadTestResult:
    total_requests: int = 0
    successful: int = 0
    failed: int = 0
    errors: list = field(default_factory=list)
    latencies_ms: list = field(default_factory=list)
    routes_used: dict = field(default_factory=dict)
    start_time: float = 0
    end_time: float = 0

    @property
    def duration_s(self):
        return self.end_time - self.start_time

    @property
    def rps(self):
        return self.total_requests / self.duration_s if self.duration_s > 0 else 0

    @property
    def error_rate(self):
        return self.failed / self.total_requests * 100 if self.total_requests > 0 else 0

    def summary(self) -> str:
        lat = self.latencies_ms
        lines = [
            f"\n{'='*60}",
            f"LOAD TEST RESULTS",
            f"{'='*60}",
            f"Duration:        {self.duration_s:.1f}s",
            f"Total requests:  {self.total_requests}",
            f"Successful:      {self.successful} ({100 - self.error_rate:.1f}%)",
            f"Failed:          {self.failed} ({self.error_rate:.1f}%)",
            f"Throughput:      {self.rps:.2f} req/s",
            "",
            "Latency (ms):",
        ]
        if lat:
            sorted_lat = sorted(lat)
            lines += [
                f"  Min:    {min(lat):.0f}",
                f"  Max:    {max(lat):.0f}",
                f"  Mean:   {statistics.mean(lat):.0f}",
                f"  Median: {statistics.median(lat):.0f}",
            ]
            if len(lat) > 20:
                lines.append(f"  P95:    {sorted_lat[int(len(lat)*0.95)]:.0f}")
            if len(lat) > 100:
                lines.append(f"  P99:    {sorted_lat[int(len(lat)*0.99)]:.0f}")
        else:
            lines.append("  No data")

        lines += ["", "Routes used:"]
        for route, count in sorted(self.routes_used.items(), key=lambda x: -x[1]):
            lines.append(f"  {route}: {count}")

        if self.errors:
            lines += ["", "Errors (first 5):"]
            for err in self.errors[:5]:
                lines.append(f"  • {err}")

        lines.append(f"{'='*60}")
        return "\n".join(lines)


async def simulate_user(user_id: int, token: str, queries: list, result: LoadTestResult,
                        streaming: bool = False, session_id: str = None):
    async with httpx.AsyncClient(timeout=180.0) as client:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        for query in queries:
            start = time.time()
            try:
                if streaming:
                    async with client.stream(
                        "POST", f"{API_BASE}/api/chat", headers=headers,
                        json={"message": query, "session_id": session_id, "stream": True}
                    ) as resp:
                        route = "STREAMED"
                        async for line in resp.aiter_lines():
                            if line.startswith("data: "):
                                try:
                                    event = json.loads(line[6:])
                                    if event.get("type") == "done":
                                        break
                                except Exception:
                                    pass
                        latency = (time.time() - start) * 1000
                        result.latencies_ms.append(latency)
                        result.successful += 1
                        result.routes_used[route] = result.routes_used.get(route, 0) + 1
                else:
                    resp = await client.post(
                        f"{API_BASE}/api/chat", headers=headers,
                        json={"message": query, "session_id": session_id, "stream": False}
                    )
                    latency = (time.time() - start) * 1000
                    result.latencies_ms.append(latency)
                    if resp.status_code == 200:
                        data = resp.json()
                        route = data.get("route_used", "unknown")
                        result.successful += 1
                        result.routes_used[route] = result.routes_used.get(route, 0) + 1
                    else:
                        result.failed += 1
                        result.errors.append(f"User {user_id}: HTTP {resp.status_code} on '{query[:40]}'")

            except Exception as e:
                result.failed += 1
                result.errors.append(f"User {user_id}: {str(e)[:80]}")

            result.total_requests += 1


async def run_load_test(num_users: int, queries_per_user: int, streaming: bool = False):
    print(f"\n\033[94mKCCITM Load Test\033[0m")
    print(f"Users: {num_users} | Queries/user: {queries_per_user} | Streaming: {streaming}")
    print(f"Total requests: {num_users * queries_per_user}")
    print("Logging in...")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{API_BASE}/api/auth/login",
                                 json={"username": "admin", "password": "admin123"})
        if resp.status_code != 200:
            print(f"\033[91mLogin failed: {resp.text}\033[0m")
            return
        token = resp.json()["access_token"]

    # Create sessions
    sessions = []
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {token}"}
        for _ in range(num_users):
            try:
                resp = await client.post(f"{API_BASE}/api/sessions", headers=headers)
                sessions.append(resp.json().get("session_id", ""))
            except Exception:
                sessions.append("")

    user_queries = [
        [random.choice(QUERY_POOL) for _ in range(queries_per_user)]
        for _ in range(num_users)
    ]

    result = LoadTestResult()
    result.start_time = time.time()

    print(f"Starting {num_users} concurrent users...\n")
    tasks = [
        simulate_user(i, token, user_queries[i], result, streaming, sessions[i])
        for i in range(num_users)
    ]
    await asyncio.gather(*tasks)
    result.end_time = time.time()

    print(result.summary())

    # Cleanup
    async with httpx.AsyncClient(timeout=10) as client:
        headers = {"Authorization": f"Bearer {token}"}
        for sid in sessions:
            if sid:
                try:
                    await client.delete(f"{API_BASE}/api/sessions/{sid}", headers=headers)
                except Exception:
                    pass

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KCCITM Load Test")
    parser.add_argument("--users",     type=int, default=10, help="Concurrent users")
    parser.add_argument("--queries",   type=int, default=5,  help="Queries per user")
    parser.add_argument("--streaming", action="store_true",  help="Test SSE streaming")
    args = parser.parse_args()
    asyncio.run(run_load_test(args.users, args.queries, args.streaming))

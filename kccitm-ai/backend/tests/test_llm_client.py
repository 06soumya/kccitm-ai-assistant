"""
LLM Client smoke test.

Verifies all OllamaClient methods work correctly against a live Ollama instance.
Requires Ollama running with qwen3:8b and nomic-embed-text.

Run:
    cd backend
    python -m tests.test_llm_client
"""

import asyncio
import json
import sys

from core.llm_client import OllamaClient

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"


async def run_tests() -> None:
    llm = OllamaClient()

    # ── Test 1: Health check ──────────────────────────────────────────────────
    print("Test 1: Health check...")
    health = await llm.health_check()
    assert health["status"] == "ok", f"Ollama not running: {health}"
    print(f"  {GREEN}✓{RESET} Ollama running. Models: {health['models']}")

    # ── Test 2: Simple generation ─────────────────────────────────────────────
    print("\nTest 2: Simple generation...")
    response = await llm.generate("What is 2+2? Reply with just the number.")
    assert "4" in response, f"Unexpected response: {response}"
    print(f"  {GREEN}✓{RESET} Generation works: '{response.strip()[:50]}'")

    # ── Test 3: JSON format generation ────────────────────────────────────────
    print("\nTest 3: JSON format generation...")
    response = await llm.generate(
        'Return a JSON object with keys "name" and "age" for a 25-year-old named Alice.',
        format="json",
        temperature=0.1,
    )
    parsed = json.loads(response)
    assert "name" in parsed and "age" in parsed, f"Bad JSON: {response}"
    print(f"  {GREEN}✓{RESET} JSON mode works: {parsed}")

    # ── Test 4: Chat completion ───────────────────────────────────────────────
    print("\nTest 4: Chat completion...")
    messages = [
        {"role": "system", "content": "You are a helpful assistant. Be very brief."},
        {"role": "user", "content": "What is Python?"},
    ]
    response = await llm.chat(messages)
    assert len(response) > 10, f"Response too short: {response}"
    print(f"  {GREEN}✓{RESET} Chat works: '{response.strip()[:80]}...'")

    # ── Test 5: Streaming ─────────────────────────────────────────────────────
    print("\nTest 5: Streaming...")
    stream_messages = [
        {"role": "user", "content": "Count from 1 to 5, one number per line."},
    ]
    tokens: list[str] = []
    async for token in llm.stream_chat(stream_messages):
        tokens.append(token)
    full = "".join(tokens)
    assert "1" in full and "5" in full, f"Streaming incomplete: {full[:200]}"
    print(f"  {GREEN}✓{RESET} Streaming works: {len(tokens)} tokens received")

    # ── Test 6: Embedding ─────────────────────────────────────────────────────
    print("\nTest 6: Embedding...")
    embedding = await llm.embed("Hello world")
    assert len(embedding) == 768, f"Expected 768-dim, got {len(embedding)}"
    assert all(isinstance(x, float) for x in embedding[:5]), \
        f"Expected floats, got {[type(x) for x in embedding[:5]]}"
    print(f"  {GREEN}✓{RESET} Embedding works: {len(embedding)}-dim vector")

    # ── Test 7: Batch embedding ───────────────────────────────────────────────
    print("\nTest 7: Batch embedding...")
    embeddings = await llm.embed_batch(["Hello", "World", "Test"])
    assert len(embeddings) == 3, f"Expected 3 vectors, got {len(embeddings)}"
    assert all(len(e) == 768 for e in embeddings), \
        f"Not all vectors are 768-dim: {[len(e) for e in embeddings]}"
    print(f"  {GREEN}✓{RESET} Batch embedding works: {len(embeddings)} vectors")

    print(f"\n{GREEN}✓ All LLM client tests passed!{RESET}")


if __name__ == "__main__":
    asyncio.run(run_tests())

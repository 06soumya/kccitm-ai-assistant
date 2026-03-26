"""
Embedding Generator for KCCITM AI Assistant.

Uses Ollama's nomic-embed-text model to embed all chunk texts.
Saves results to:
  - data/embeddings.jsonl  — {chunk_id, embedding} per line
  - data/embeddings.npy    — numpy array (shape: N × 768)
  - data/chunk_ids.json    — ordered list of chunk_ids matching npy rows

Usage:
    cd backend
    python -m ingestion.embedder

Requirements:
    Ollama must be running with nomic-embed-text pulled:
        ollama pull nomic-embed-text
"""

import asyncio
import json
import logging
import os
import time
from typing import Any

import httpx
import numpy as np
from tqdm import tqdm

from config import settings
from ingestion.chunker import generate_chunks, load_chunks_from_file, CHUNKS_FILE

# ── ANSI colours ─────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = "data"
EMBEDDINGS_JSONL = os.path.join(DATA_DIR, "embeddings.jsonl")
EMBEDDINGS_NPY   = os.path.join(DATA_DIR, "embeddings.npy")
CHUNK_IDS_JSON   = os.path.join(DATA_DIR, "chunk_ids.json")

EMBED_BATCH_SIZE = 10
MAX_RETRIES      = 3
RETRY_DELAYS     = [1, 3, 9]  # exponential backoff in seconds


# ── Core embedding function ───────────────────────────────────────────────────

async def embed_text(
    text: str,
    ollama_host: str,
    model: str,
) -> list[float]:
    """
    Embed a single text using Ollama's /api/embeddings endpoint.

    Args:
        text: Text to embed
        ollama_host: Ollama base URL, e.g. 'http://localhost:11434'
        model: Embedding model name, e.g. 'nomic-embed-text'

    Returns:
        List of floats (embedding vector).
    """
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{ollama_host}/api/embeddings",
            json={"model": model, "prompt": text},
        )
        response.raise_for_status()
        return response.json()["embedding"]


async def embed_batch(
    texts: list[str],
    ollama_host: str,
    model: str,
) -> list[list[float]]:
    """Embed a batch of texts concurrently."""
    tasks = [embed_text(t, ollama_host, model) for t in texts]
    return await asyncio.gather(*tasks)


async def embed_with_retry(
    text: str,
    ollama_host: str,
    model: str,
    chunk_id: str,
) -> list[float] | None:
    """
    Embed a single text with retry on connection errors.

    Returns the embedding or None on persistent failure.
    """
    for attempt, delay in enumerate(RETRY_DELAYS, 1):
        try:
            return await embed_text(text, ollama_host, model)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as exc:
            if attempt < MAX_RETRIES:
                logger.warning(
                    "Embedding failed for %s (attempt %d/%d): %s — retrying in %ds",
                    chunk_id, attempt, MAX_RETRIES, exc, delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.error("Embedding permanently failed for %s: %s", chunk_id, exc)
                return None
    return None


# ── Main embedding pipeline ───────────────────────────────────────────────────

async def embed_all_chunks(chunks: list[tuple[str, dict]]) -> None:
    """
    Embed all chunks and save results to data/.

    Args:
        chunks: List of (text, metadata) tuples from chunker.
    """
    os.makedirs(DATA_DIR, exist_ok=True)

    total = len(chunks)
    ollama_host = settings.OLLAMA_HOST
    model = settings.OLLAMA_EMBED_MODEL
    dim = settings.OLLAMA_EMBED_DIM

    print(f"Embedding {total} chunks using {model} @ {ollama_host}")
    print(f"Batch size: {EMBED_BATCH_SIZE}, Estimated dim: {dim}")

    embeddings_list: list[list[float]] = []
    chunk_ids: list[str] = []
    failed = 0

    start = time.time()

    with open(EMBEDDINGS_JSONL, "w", encoding="utf-8") as f_out:
        with tqdm(total=total, unit="chunk", desc="Embedding") as pbar:
            for i in range(0, total, EMBED_BATCH_SIZE):
                batch = chunks[i : i + EMBED_BATCH_SIZE]

                batch_texts = [text for text, _ in batch]
                batch_metas = [meta for _, meta in batch]

                # Embed each text in this batch (with per-item retry)
                embed_tasks = [
                    embed_with_retry(
                        text=text,
                        ollama_host=ollama_host,
                        model=model,
                        chunk_id=meta["chunk_id"],
                    )
                    for text, meta in batch
                ]
                results = await asyncio.gather(*embed_tasks)

                for meta, embedding in zip(batch_metas, results):
                    cid = meta["chunk_id"]
                    if embedding is None:
                        failed += 1
                        pbar.update(1)
                        continue

                    embeddings_list.append(embedding)
                    chunk_ids.append(cid)

                    # Write to JSONL
                    f_out.write(
                        json.dumps({"chunk_id": cid, "embedding": embedding}) + "\n"
                    )

                pbar.update(len(batch))

    elapsed = time.time() - start
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)

    # Save numpy array
    arr = np.array(embeddings_list, dtype=np.float32)
    np.save(EMBEDDINGS_NPY, arr)

    # Save chunk ID ordering
    with open(CHUNK_IDS_JSON, "w", encoding="utf-8") as f:
        json.dump(chunk_ids, f)

    if failed:
        print(f"{YELLOW}⚠ {failed} chunks failed to embed and were skipped{RESET}")

    print(
        f"\n{GREEN}✓ Embedded {len(chunk_ids)} chunks in {minutes}m {seconds}s. "
        f"Saved to {DATA_DIR}/{RESET}"
    )
    print(f"  embeddings.jsonl  — {len(chunk_ids)} records")
    print(f"  embeddings.npy    — shape {arr.shape}")
    print(f"  chunk_ids.json    — {len(chunk_ids)} IDs")


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    # Load chunks: from file if available (faster), else regenerate
    if os.path.exists(CHUNKS_FILE):
        print(f"Loading chunks from {CHUNKS_FILE}...")
        chunks = load_chunks_from_file()
        print(f"Loaded {len(chunks)} chunks.")
    else:
        print("Chunks file not found. Regenerating from MySQL...")
        from ingestion.chunker import generate_chunks, save_chunks
        chunks = generate_chunks()
        save_chunks(chunks)

    asyncio.run(embed_all_chunks(chunks))

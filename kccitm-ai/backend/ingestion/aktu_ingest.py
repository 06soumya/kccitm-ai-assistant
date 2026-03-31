"""
Ingest AKTU knowledge files into a SEPARATE Milvus collection.

Reads .txt files from data/aktu_knowledge/, chunks them, embeds via
nomic-embed-text, and stores in the 'aktu_knowledge' collection.

Does NOT modify the existing student_results or faq collections.

Usage:
    cd backend
    python -m ingestion.aktu_ingest
"""

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path

from pymilvus import MilvusClient, DataType, Function, FunctionType

from config import settings

# ── Config ────────────────────────────────────────────────────────────────────

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

AKTU_DIR = Path("data/aktu_knowledge")
COLLECTION = settings.MILVUS_AKTU_COLLECTION
EMBED_DIM = settings.OLLAMA_EMBED_DIM
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
MIN_FILE_SIZE = 200  # skip files smaller than this


# ── Chunker ───────────────────────────────────────────────────────────────────

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks, preferring paragraph breaks."""
    # Split on double newlines first (paragraph boundaries)
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    chunks = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 1 <= chunk_size:
            current = (current + "\n\n" + para).strip() if current else para
        else:
            if current:
                chunks.append(current)
            # If single paragraph exceeds chunk_size, split by sentences
            if len(para) > chunk_size:
                words = para.split()
                current = ""
                for word in words:
                    if len(current) + len(word) + 1 <= chunk_size:
                        current = (current + " " + word).strip() if current else word
                    else:
                        if current:
                            chunks.append(current)
                        current = word
            else:
                current = para

    if current:
        chunks.append(current)

    # Add overlap: prepend last N chars of previous chunk to each chunk
    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_tail = chunks[i - 1][-overlap:]
            overlapped.append(prev_tail + " " + chunks[i])
        chunks = overlapped

    return chunks


def classify_content(filename: str) -> str:
    """Classify content type from filename."""
    name = filename.lower()
    if "syllabus" in name:
        return "syllabus"
    elif "ordinance" in name:
        return "regulation"
    elif "calendar" in name:
        return "notification"
    elif "notification" in name:
        return "notification"
    elif "cse" in name:
        return "syllabus"
    else:
        return "general"


# ── Milvus collection setup ──────────────────────────────────────────────────

def create_aktu_collection(client: MilvusClient) -> None:
    """Create the aktu_knowledge collection (separate from student data)."""
    if client.has_collection(COLLECTION):
        logger.info("Dropping existing %s collection for re-ingestion", COLLECTION)
        client.drop_collection(COLLECTION)

    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)

    schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=100)
    schema.add_field("text", DataType.VARCHAR, max_length=10000, enable_analyzer=True)
    schema.add_field("dense", DataType.FLOAT_VECTOR, dim=EMBED_DIM)
    schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)

    # Metadata
    schema.add_field("source_file", DataType.VARCHAR, max_length=255)
    schema.add_field("content_type", DataType.VARCHAR, max_length=50)
    schema.add_field("chunk_index", DataType.INT64)

    # BM25 function
    schema.add_function(Function(
        name="bm25_fn",
        function_type=FunctionType.BM25,
        input_field_names=["text"],
        output_field_names=["sparse"],
    ))

    # Indexes
    index_params = client.prepare_index_params()
    index_params.add_index(field_name="dense", index_type="AUTOINDEX", metric_type="COSINE")
    index_params.add_index(field_name="sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")

    client.create_collection(collection_name=COLLECTION, schema=schema, index_params=index_params)
    logger.info("%sCreated collection: %s%s", GREEN, COLLECTION, RESET)


# ── Embedding ─────────────────────────────────────────────────────────────────

async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts using our existing Ollama client."""
    from core.llm_client import OllamaClient
    llm = OllamaClient()
    embeddings = []
    for i, text in enumerate(texts):
        emb = await llm.embed(text)
        embeddings.append(emb)
        if (i + 1) % 50 == 0:
            print(f"    Embedded {i + 1}/{len(texts)} chunks...")
    return embeddings


# ── Main ingestion ────────────────────────────────────────────────────────────

async def ingest():
    """Read, chunk, embed, and store AKTU knowledge files."""
    print(f"\n{GREEN}AKTU Knowledge Ingestion{RESET}")
    print(f"Source: {AKTU_DIR}")
    print(f"Collection: {COLLECTION}")
    print()

    # 1. Read text files
    txt_files = sorted(AKTU_DIR.glob("*.txt"))
    if not txt_files:
        print(f"{RED}No .txt files found in {AKTU_DIR}{RESET}")
        return

    all_chunks = []
    files_processed = 0

    for txt_file in txt_files:
        size = txt_file.stat().st_size
        if size < MIN_FILE_SIZE:
            print(f"  {YELLOW}Skip{RESET} {txt_file.name} ({size} bytes < {MIN_FILE_SIZE})")
            continue

        text = txt_file.read_text(encoding="utf-8", errors="replace")
        chunks = chunk_text(text)
        content_type = classify_content(txt_file.name)

        for idx, chunk in enumerate(chunks):
            all_chunks.append({
                "chunk_id": str(uuid.uuid4()),
                "text": chunk[:9999],  # max_length safety
                "source_file": txt_file.name,
                "content_type": content_type,
                "chunk_index": idx,
            })

        files_processed += 1
        print(f"  {GREEN}Read{RESET} {txt_file.name}: {len(chunks)} chunks ({size:,} bytes, {content_type})")

    print(f"\nTotal: {files_processed} files, {len(all_chunks)} chunks")

    if not all_chunks:
        print(f"{RED}No chunks to ingest{RESET}")
        return

    # 2. Embed all chunks
    print(f"\nEmbedding {len(all_chunks)} chunks via nomic-embed-text...")
    texts = [c["text"] for c in all_chunks]
    embeddings = await embed_texts(texts)
    print(f"  {GREEN}Done{RESET} — {len(embeddings)} embeddings generated")

    # 3. Create collection and insert
    print(f"\nConnecting to Milvus...")
    uri = settings.milvus_uri
    client = MilvusClient(uri=uri)
    create_aktu_collection(client)

    # Build insert data
    data = []
    for chunk, emb in zip(all_chunks, embeddings):
        data.append({
            "chunk_id": chunk["chunk_id"],
            "text": chunk["text"],
            "dense": emb,
            "source_file": chunk["source_file"],
            "content_type": chunk["content_type"],
            "chunk_index": chunk["chunk_index"],
        })

    # Insert in batches
    batch_size = 100
    for i in range(0, len(data), batch_size):
        batch = data[i : i + batch_size]
        client.insert(collection_name=COLLECTION, data=batch)
        print(f"  Inserted {min(i + batch_size, len(data))}/{len(data)} chunks")

    # 4. Stats
    stats = client.get_collection_stats(COLLECTION)
    row_count = stats.get("row_count", 0)
    print(f"\n{GREEN}{'='*50}{RESET}")
    print(f"{GREEN}AKTU ingestion complete!{RESET}")
    print(f"  Files processed: {files_processed}")
    print(f"  Chunks created:  {len(all_chunks)}")
    print(f"  Milvus rows:     {row_count}")
    print(f"  Collection:      {COLLECTION}")
    print(f"{GREEN}{'='*50}{RESET}")


if __name__ == "__main__":
    asyncio.run(ingest())

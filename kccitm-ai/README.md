# KCCITM AI Assistant

Self-improving RAG + LLM academic assistant for KCCITM institute.

## Quick Start

```bash
# Start infrastructure
docker compose up -d

# Install dependencies
cd backend && pip install -r requirements.txt

# Initialize SQLite databases
python -c "from db.sqlite_client import init_all_dbs; from config import settings; init_all_dbs(settings)"

# Run ingestion pipeline
python -m ingestion.etl
python -m ingestion.chunker
python -m ingestion.embedder
python -m ingestion.milvus_indexer

# Validate
python -m ingestion.validate
```

## Architecture

- **MySQL**: Normalized student academic records
- **Milvus**: Vector + BM25 search (hybrid_search)
- **Ollama**: Local LLM + embeddings
- **SQLite**: Sessions, cache, feedback, prompts

# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

**KCCITM AI Assistant** — a self-improving RAG + LLM academic chatbot. Faculty/admin ask natural-language questions about student academic records; the system routes each query to a SQL pipeline (rankings, counts, averages), a RAG pipeline (HyDE + multi-query + Milvus hybrid search + reranking), or runs both in parallel (HYBRID). Every response feeds a per-request feedback collector and a nightly self-improvement loop (failure classification → training-candidate accumulation → optional LoRA fine-tune).

The legacy Streamlit prototype was removed in commit `02fec95`. The active system is the FastAPI backend + Next.js frontend under `kccitm-ai/`.

## Layout

```
kccitm-ai-assistant/
├── docker-compose.yml        ← MySQL + Milvus (started from repo root)
├── README.md                 ← User-facing quick start
├── data/models/Modelfile     ← Ollama Modelfile (reference)
└── kccitm-ai/
    ├── backend/              ← FastAPI + Ollama
    │   ├── main.py           ← Entry: uvicorn main:app (port 8000)
    │   ├── config.py         ← Pydantic settings (env-driven)
    │   ├── api/routes/       ← chat, auth, sessions, feedback, admin, dashboard
    │   ├── core/             ← orchestrator, router, sql_pipeline, rag_pipeline,
    │   │                       query_normalizer (Hinglish), query_understander,
    │   │                       hyde, multi_query, reranker, compressor,
    │   │                       context_builder, llm_client, openai_fallback,
    │   │                       sql_examples_store, schema_reader, session_manager,
    │   │                       cache, faq_engine, aktu_notifications
    │   ├── adaptive/         ← feedback_collector, failure_classifier,
    │   │                       quality_scorer, query_healer, prompt_evolver,
    │   │                       prompt_ab_tester, training_data_manager,
    │   │                       star_sql, faq_generator, rechunker, chunk_analyzer
    │   ├── jobs/             ← scheduler + 5 cron jobs (started in lifespan)
    │   ├── ingestion/        ← One-time ETL pipeline
    │   ├── training/         ← Offline LoRA fine-tuning (manual)
    │   ├── tools/            ← CLI utilities + runtime middleware (security, logger)
    │   ├── tests/            ← pytest suite
    │   └── db/               ← mysql_client (aiomysql), milvus_client, sqlite_client
    └── frontend/             ← Next.js 16 + React 19 + Tailwind
        └── src/app/          ← /, /chat, /admin/{prompts,models,faqs,training,
                                feedback,healing,chunks,system}
```

## Commands

**Start infrastructure** (from repo root):
```
docker compose up -d
```

**Run backend**:
```
cd kccitm-ai/backend
python3.12 -m venv venv312 && source venv312/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

**Run frontend**:
```
cd kccitm-ai/frontend
npm install && npm run dev    # http://localhost:3001
```

**Bootstrap data** (one-time, from `kccitm-ai/backend/`):
```
python -m ingestion.etl
python -m ingestion.chunker
python -m ingestion.embedder
python -m ingestion.milvus_indexer
python -m ingestion.init_prompts
python -m ingestion.create_admin
```

**Tests** (from `kccitm-ai/backend/`): `pytest tests/`

## Request pipeline

`POST /api/chat` → [`orchestrator.process_query`](kccitm-ai/backend/core/orchestrator.py):

1. Session history load (SQLite)
2. Exact + semantic cache check (SQLite)
3. FAQ engine (Milvus `faq`)
4. AKTU notifications / knowledge check (Milvus `aktu_knowledge`)
5. Meta-question detection (concept Qs go to OpenAI fallback if enabled)
6. [`query_understander.understand`](kccitm-ai/backend/core/query_understander.py) — Hinglish normalization + intent extraction
7. Student lookup short-circuit (name/roll → SQL pipeline)
8. [`router.route`](kccitm-ai/backend/core/router.py) → `SQL` | `RAG` | `HYBRID`
9. Pipeline execution (HYBRID runs both via `asyncio.gather`)
10. OpenAI fallback if local output is weak and `OPENAI_ENABLED=True`
11. Cache + session write
12. Return `QueryResponse` with `route_used`, timing, and metadata

## Model & store config

Defaults in [config.py](kccitm-ai/backend/config.py); override via `.env`.

- LLM: Ollama `kccitm-v2` at `http://localhost:11434` (draft model `qwen3:1.7b`)
- Embeddings: `nomic-embed-text` (768-dim)
- MySQL: `kccitm` DB, aiomysql pool 2–10
- Milvus: lite (`KCCITM_MILVUS_URI=<file>`) or HTTP (`MILVUS_HOST:MILVUS_PORT`); 3 collections — `student_results`, `faq`, `aktu_knowledge`
- SQLite: `data/{sessions,cache,feedback,prompts}.db`
- OpenAI fallback: disabled by default (`OPENAI_ENABLED=False`)

## Self-improvement loop

- Per-request: `feedback_collector.detect_implicit_signals`, `failure_classifier.classify_sql_error`
- Daily 02:00 UTC: healing — consolidates feedback, classifies failures, accumulates training candidates
- Daily 02:30 UTC: STaR-SQL — rationalizes user-corrected SQL into training pairs
- Daily 03:00 UTC: FAQ generation from successful queries
- Weekly Sun 03:00 UTC: prompt evolution + A/B-test evaluation
- Monthly (manual): LoRA fine-tune via `training/train_lora.py` when ≥500 pairs collected

## Editing notes

- `core/orchestrator.py` (~1700 LOC) and `core/sql_pipeline.py` (~1660 LOC) are the largest modules — change carefully.
- New routes go in `api/routes/` and must be mounted in `main.py`.
- Per-request adaptive logic must be wired into `orchestrator.py` or `sql_pipeline.py`. Batch logic goes in `jobs/` and is registered in `jobs/scheduler.py`.
- Frontend backend URL is `NEXT_PUBLIC_API_URL`; admin calls go through `src/lib/adminApi.ts`, chat through `src/lib/api.ts` and `src/lib/sse.ts`.
- MySQL uses defaults; add a `./mysql-custom.cnf` mount under the `mysql_kccitm` service in [docker-compose.yml](docker-compose.yml) if you need to override server config.

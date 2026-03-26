"""
KCCITM AI Assistant — FastAPI Backend

Start:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Docs:
    http://localhost:8000/docs
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.deps import get_llm, get_milvus, init_services
from api.routes import admin, auth, chat, dashboard, feedback, sessions
from jobs.scheduler import scheduler, setup_scheduler
from tools.logger import setup_logging, RequestLoggingMiddleware
from tools.security import SecurityMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize all services at startup; clean up at shutdown."""
    setup_logging()
    print("Initializing services...")
    init_services()
    setup_scheduler()
    print("Services + scheduler initialized. Server ready.")
    yield
    print("Shutting down...")
    scheduler.shutdown(wait=False)


app = FastAPI(
    title="KCCITM AI Assistant",
    description="Self-improving RAG + LLM API for academic data",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(SecurityMiddleware)

# CORS — allow Next.js frontend (port 3000) and local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "*",  # Restrict in production
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(auth.router,     prefix="/api/auth",     tags=["Auth"])
app.include_router(chat.router,     prefix="/api",          tags=["Chat"])
app.include_router(sessions.router, prefix="/api/sessions", tags=["Sessions"])
app.include_router(admin.router,    prefix="/api/admin",    tags=["Admin"])
app.include_router(feedback.router,   prefix="/api",          tags=["Feedback"])
app.include_router(dashboard.router,  prefix="/api/admin",    tags=["Dashboard"])


@app.get("/api/health", tags=["Health"])
async def health_check():
    """
    System health check — verifies Ollama, MySQL, and Milvus are reachable.
    No authentication required.
    """
    from db.mysql_client import execute_query

    checks: dict = {}

    # Ollama
    try:
        llm = get_llm()
        health = await llm.health_check()
        checks["ollama"] = {"status": "ok", "models": health.get("models", [])}
    except Exception as exc:
        checks["ollama"] = {"status": "error", "message": str(exc)}

    # MySQL
    try:
        await execute_query("SELECT 1 AS ok")
        checks["mysql"] = {"status": "ok"}
    except Exception as exc:
        checks["mysql"] = {"status": "error", "message": str(exc)}

    # Milvus
    try:
        milvus = get_milvus()
        stats = milvus.get_collection_stats()
        checks["milvus"] = {"status": "ok", "chunks": stats.get("num_entities", 0)}
    except Exception as exc:
        checks["milvus"] = {"status": "error", "message": str(exc)}

    all_ok = all(c.get("status") == "ok" for c in checks.values())

    return {
        "status": "healthy" if all_ok else "degraded",
        "services": checks,
    }

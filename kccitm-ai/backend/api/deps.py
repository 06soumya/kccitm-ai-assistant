"""
Dependency injection for FastAPI routes.
Creates singleton service instances at app startup, provides them via Depends().

All services are created ONCE in init_services() (called from lifespan in main.py).
Routes access them through the get_*() functions below.
"""
from config import settings
from core.cache import QueryCache
from core.llm_client import OllamaClient
from core.orchestrator import Orchestrator
from core.rag_pipeline import RAGPipeline
from core.router import QueryRouter
from core.session_manager import SessionManager
from core.sql_pipeline import SQLPipeline
from db.milvus_client import MilvusSearchClient

# Singleton instances
_llm: OllamaClient | None = None
_router: QueryRouter | None = None
_sql_pipeline: SQLPipeline | None = None
_rag_pipeline: RAGPipeline | None = None
_orchestrator: Orchestrator | None = None
_session_manager: SessionManager | None = None
_cache: QueryCache | None = None
_milvus: MilvusSearchClient | None = None


def init_services() -> None:
    """Initialize all services. Called once at app startup via lifespan."""
    global _llm, _router, _sql_pipeline, _rag_pipeline
    global _orchestrator, _session_manager, _cache, _milvus

    _llm = OllamaClient()
    _router = QueryRouter(_llm)
    _sql_pipeline = SQLPipeline(_llm)
    _milvus = MilvusSearchClient(uri=settings.milvus_uri)
    _rag_pipeline = RAGPipeline(_llm, _milvus)
    _session_manager = SessionManager()
    _cache = QueryCache(_llm)
    _orchestrator = Orchestrator(
        _llm, _router, _sql_pipeline, _rag_pipeline,
        _milvus, _session_manager, _cache,
    )


def get_orchestrator() -> Orchestrator:
    assert _orchestrator is not None, "Services not initialized. Call init_services() first."
    return _orchestrator


def get_session_manager() -> SessionManager:
    assert _session_manager is not None, "Services not initialized."
    return _session_manager


def get_cache() -> QueryCache:
    assert _cache is not None, "Services not initialized."
    return _cache


def get_llm() -> OllamaClient:
    assert _llm is not None, "Services not initialized."
    return _llm


def get_milvus() -> MilvusSearchClient:
    assert _milvus is not None, "Services not initialized."
    return _milvus

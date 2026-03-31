"""
Central configuration for KCCITM AI Assistant.

All settings are loaded from environment variables / .env file.
Import pattern: from config import settings
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All project-wide settings. Override via .env file or environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── MySQL ─────────────────────────────────────────────────────────────────
    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_DB: str = "kccitm"
    MYSQL_USER: str = "user"
    MYSQL_PASSWORD: str = "qCsfeuECc3MW"

    # ── Milvus ────────────────────────────────────────────────────────────────
    # Set KCCITM_MILVUS_URI to a file path (e.g. "data/milvus.db") to use
    # milvus-lite without Docker. Leave empty to use MILVUS_HOST:MILVUS_PORT.
    # NOTE: Do NOT use MILVUS_URI — that name is reserved by pymilvus internals.
    KCCITM_MILVUS_URI: str = ""
    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530
    MILVUS_COLLECTION: str = "student_results"
    MILVUS_FAQ_COLLECTION: str = "faq"

    @property
    def milvus_uri(self) -> str:
        """Resolved URI — file path for milvus-lite, HTTP otherwise."""
        if self.KCCITM_MILVUS_URI:
            return self.KCCITM_MILVUS_URI
        return f"http://{self.MILVUS_HOST}:{self.MILVUS_PORT}"

    # ── Ollama ────────────────────────────────────────────────────────────────
    OLLAMA_HOST: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen3:8b"
    OLLAMA_DRAFT_MODEL: str = "qwen3:1.7b"
    OLLAMA_EMBED_MODEL: str = "nomic-embed-text"
    OLLAMA_EMBED_DIM: int = 768

    # ── SQLite database paths ─────────────────────────────────────────────────
    SESSION_DB: str = "data/sessions.db"
    CACHE_DB: str = "data/cache.db"
    FEEDBACK_DB: str = "data/feedback.db"
    PROMPTS_DB: str = "data/prompts.db"

    # ── Auth ──────────────────────────────────────────────────────────────────
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_HOURS: int = 24

    # ── RAG settings ──────────────────────────────────────────────────────────
    RAG_TOP_K: int = 30
    RAG_RERANK_TOP_K: int = 7
    RERANKER_MODEL: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    RAG_USE_OPTIMIZATIONS: bool = True   # Set False to use basic Phase 4 RAG
    CACHE_SIMILARITY_THRESHOLD: float = 0.88
    CACHE_TTL_HOURS: int = 24

    # ── LLM settings ─────────────────────────────────────────────────────────
    LLM_TEMPERATURE: float = 0.3
    LLM_MAX_TOKENS: int = 2048
    LLM_NUM_CTX: int = 32768

    # ── SQL pipeline ──────────────────────────────────────────────────────────
    USE_DYNAMIC_SCHEMA: bool = True   # True = auto-discover schema; False = hardcoded
    LOGICCAT_MODE: bool = False       # True only for benchmark evaluation (extra retries, extended prompts)


# Singleton instance — import this everywhere
settings = Settings()

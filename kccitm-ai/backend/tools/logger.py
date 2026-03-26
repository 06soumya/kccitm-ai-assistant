"""
Structured JSON logging with request tracing.
Every request gets a unique trace_id that flows through all log entries.

Setup — in main.py:
    from tools.logger import setup_logging, RequestLoggingMiddleware
    setup_logging()                           # call inside lifespan or at module level
    app.add_middleware(RequestLoggingMiddleware)
"""
import logging
import json
import uuid
import time
import sys
from datetime import datetime
from contextvars import ContextVar
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="none")


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level":     record.levelname,
            "message":   record.getMessage(),
            "logger":    record.name,
            "trace_id":  trace_id_var.get("none"),
        }
        if hasattr(record, "extra_data"):
            entry.update(record.extra_data)
        if record.exc_info and record.exc_info[0]:
            entry["exception"] = {
                "type":    record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
            }
        return json.dumps(entry, default=str)


def setup_logging(log_file: str = "data/app.log") -> logging.Logger:
    """Configure structured JSON logging to stdout + file."""
    from pathlib import Path
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    formatter = JSONFormatter()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    # Avoid adding duplicate handlers on reload
    if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        root.addHandler(console)
        root.addHandler(file_handler)
    root.setLevel(logging.INFO)

    # Silence noisy libraries
    for noisy in ("httpx", "httpcore", "uvicorn.access", "pymilvus"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return root


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request with timing and trace ID."""

    async def dispatch(self, request: Request, call_next):
        tid = str(uuid.uuid4())[:8]
        trace_id_var.set(tid)

        logger = logging.getLogger("api")
        start  = time.time()

        logger.info("request_start", extra={"extra_data": {
            "method": request.method,
            "path":   request.url.path,
            "client": request.client.host if request.client else "unknown",
        }})

        try:
            response = await call_next(request)
            elapsed  = (time.time() - start) * 1000
            logger.info("request_done", extra={"extra_data": {
                "method":      request.method,
                "path":        request.url.path,
                "status":      response.status_code,
                "duration_ms": round(elapsed, 1),
            }})
            response.headers["X-Trace-ID"] = tid
            return response
        except Exception as exc:
            elapsed = (time.time() - start) * 1000
            logger.error("request_error", exc_info=True, extra={"extra_data": {
                "method":      request.method,
                "path":        request.url.path,
                "duration_ms": round(elapsed, 1),
                "error":       str(exc),
            }})
            raise


def log_query(query: str, route: str, duration_ms: float,
              cache_hit: bool = False, **kwargs) -> None:
    """Log a processed query — call this from the orchestrator."""
    logging.getLogger("query").info("query_processed", extra={"extra_data": {
        "query":       query[:100],
        "route":       route,
        "duration_ms": round(duration_ms, 1),
        "cache_hit":   cache_hit,
        **kwargs,
    }})

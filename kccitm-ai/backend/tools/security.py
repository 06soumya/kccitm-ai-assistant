"""
Security middleware: rate limiting, input sanitization, abuse detection.

Setup — in main.py (add BEFORE CORSMiddleware):
    from tools.security import SecurityMiddleware
    app.add_middleware(SecurityMiddleware)
"""
import re
import time
import json
import logging
from collections import defaultdict
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger("security")


class RateLimiter:
    """Token-bucket rate limiter — per IP and per user."""

    def __init__(self):
        self._ip_buckets:   dict[str, list] = defaultdict(list)
        self._user_buckets: dict[str, list] = defaultdict(list)
        self.ip_limit    = 60   # requests per minute per IP
        self.ip_window   = 60
        self.user_limit  = 30   # chat requests per minute per user
        self.user_window = 60

    def _prune(self, bucket: list, window: float) -> list:
        now = time.time()
        return [t for t in bucket if now - t < window]

    def check_ip(self, ip: str) -> bool:
        bucket = self._prune(self._ip_buckets[ip], self.ip_window)
        if len(bucket) >= self.ip_limit:
            return False
        bucket.append(time.time())
        self._ip_buckets[ip] = bucket
        return True

    def check_user(self, user_id: str) -> bool:
        bucket = self._prune(self._user_buckets[user_id], self.user_window)
        if len(bucket) >= self.user_limit:
            return False
        bucket.append(time.time())
        self._user_buckets[user_id] = bucket
        return True


class InputSanitizer:
    MAX_MESSAGE_LENGTH  = 2000
    MAX_FEEDBACK_LENGTH = 500
    MAX_USERNAME_LENGTH = 50

    # Patterns that indicate injection attempts (not natural-language questions)
    _INJECTION = [
        r"<script[\s>]",
        r"javascript\s*:",
        r"on\w+\s*=\s*['\"]",
        r"\bDROP\s+TABLE\b",
        r"\bDELETE\s+FROM\b",
        r"\bINSERT\s+INTO\b",
        r"\bUPDATE\s+\S+\s+SET\b",
        r";\s*--",
        r"/\*.*?\*/",
        r"\bEXEC\s*\(",
        r"\bUNION\s+SELECT\b",
    ]
    _compiled = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _INJECTION]

    @classmethod
    def sanitize_message(cls, text: str) -> str:
        return text[:cls.MAX_MESSAGE_LENGTH].strip() if text else ""

    @classmethod
    def check_for_abuse(cls, text: str) -> str | None:
        """Return pattern description if suspicious, else None."""
        for pat, compiled in zip(cls._INJECTION, cls._compiled):
            if compiled.search(text):
                return pat
        return None

    @classmethod
    def sanitize_username(cls, text: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_]", "", text[:cls.MAX_USERNAME_LENGTH])


_limiter = RateLimiter()


def _json_response(detail: str, status: int) -> Response:
    return Response(
        content=json.dumps({"detail": detail}),
        status_code=status,
        media_type="application/json",
    )


class SecurityMiddleware(BaseHTTPMiddleware):
    """Combined rate limiting + abuse detection middleware."""

    # Paths that bypass rate limiting (health checks, static assets)
    _EXEMPT = {"/api/health", "/docs", "/openapi.json", "/redoc"}

    async def dispatch(self, request: Request, call_next):
        ip = request.client.host if request.client else "unknown"
        path = request.url.path

        if path not in self._EXEMPT:
            if not _limiter.check_ip(ip):
                logger.warning("rate_limited_ip", extra={"extra_data": {"ip": ip, "path": path}})
                return _json_response("Too many requests. Please slow down.", 429)

        # Body inspection for chat messages
        if request.method == "POST" and "/chat" in path:
            try:
                body_bytes = await request.body()
                if body_bytes:
                    data = json.loads(body_bytes)
                    message = data.get("message", "")

                    if len(message) > InputSanitizer.MAX_MESSAGE_LENGTH:
                        return _json_response(
                            f"Message too long (max {InputSanitizer.MAX_MESSAGE_LENGTH} chars)", 400
                        )

                    abuse = InputSanitizer.check_for_abuse(message)
                    if abuse:
                        logger.warning("abuse_detected", extra={"extra_data": {
                            "ip":      ip,
                            "preview": message[:60],
                            "pattern": abuse,
                        }})

                    # Re-inject body so downstream handlers can still read it
                    async def _body_stream():
                        yield body_bytes

                    request._body = body_bytes  # type: ignore[attr-defined]
            except Exception:
                pass  # Never block on parse errors

        return await call_next(request)

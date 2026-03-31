"""
AKTU Notification caching service.

Fetches notifications via OpenAI (primary) or AKTU website (fallback),
caches in SQLite, serves cached data.
Daily refresh at midnight via scheduler. Force-fetch on user request.

SAFETY: This is a standalone file. Does NOT modify any existing code.
"""

import hashlib
import logging
import uuid
from datetime import datetime
from typing import Optional

import httpx

from config import settings
from db.sqlite_client import execute, fetch_one

logger = logging.getLogger(__name__)

AKTU_NOTIFICATIONS_URL = "https://aktu.ac.in/circulars.html"
CACHE_VALIDITY_HOURS = 7


class AKTUNotificationService:
    """Fetches, caches, and serves AKTU notifications."""

    async def _ensure_table(self):
        """Create cache table if not exists."""
        await execute(
            settings.FEEDBACK_DB,
            """CREATE TABLE IF NOT EXISTS aktu_notification_cache (
                id TEXT PRIMARY KEY,
                url TEXT,
                content TEXT,
                content_hash TEXT,
                fetched_at TEXT,
                is_latest INTEGER DEFAULT 1
            )""",
        )

    async def fetch_from_openai(self) -> Optional[str]:
        """Ask OpenAI for latest AKTU notifications (primary source)."""
        if not settings.OPENAI_ENABLED or not settings.OPENAI_API_KEY:
            return None

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.OPENAI_MODEL,
                        "max_tokens": 1000,
                        "temperature": 0.3,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "You are an assistant that provides information about AKTU "
                                    "(Dr. A.P.J. Abdul Kalam Technical University, Lucknow, UP, India). "
                                    "Provide the latest notifications, circulars, exam schedules, and "
                                    "important announcements. Be specific with dates and details."
                                ),
                            },
                            {
                                "role": "user",
                                "content": (
                                    "What are the latest AKTU notifications, circulars, and important "
                                    "announcements for B.Tech students? Include any recent exam schedules, "
                                    "result announcements, or policy changes."
                                ),
                            },
                        ],
                    },
                )

                if resp.status_code != 200:
                    logger.warning("OpenAI AKTU fetch: HTTP %s", resp.status_code)
                    return None

                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                if len(content) > 50:
                    logger.info("Fetched AKTU notifications via OpenAI: %d chars", len(content))
                    return content
                return None

        except Exception as e:
            logger.warning("OpenAI AKTU fetch failed: %s", e)
            return None

    async def fetch_from_website(self) -> Optional[str]:
        """Fetch from AKTU website (fallback — JS-rendered, often returns empty)."""
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                response = await client.get(
                    AKTU_NOTIFICATIONS_URL,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36"
                        )
                    },
                )
                if response.status_code != 200:
                    return None

                try:
                    from bs4 import BeautifulSoup

                    soup = BeautifulSoup(response.text, "html.parser")
                    for tag in soup(["script", "style", "nav", "header", "footer"]):
                        tag.decompose()
                    text = soup.get_text(separator="\n", strip=True)
                except ImportError:
                    import re

                    text = re.sub(r"<[^>]+>", " ", response.text)
                    text = re.sub(r"\s+", " ", text).strip()

                if len(text) < 100:
                    return None

                return text

        except Exception:
            return None

    async def update_cache(self, content: str) -> dict:
        """Compare fetched content with cached, update if new."""
        await self._ensure_table()

        new_hash = hashlib.sha256(content.encode()).hexdigest()

        existing = await fetch_one(
            settings.FEEDBACK_DB,
            "SELECT content_hash, fetched_at FROM aktu_notification_cache "
            "WHERE is_latest = 1 ORDER BY fetched_at DESC LIMIT 1",
        )

        if existing and existing["content_hash"] == new_hash:
            await execute(
                settings.FEEDBACK_DB,
                "UPDATE aktu_notification_cache SET fetched_at = ? WHERE is_latest = 1",
                (datetime.utcnow().isoformat(),),
            )
            return {"status": "unchanged", "hash": new_hash}

        await execute(
            settings.FEEDBACK_DB,
            "UPDATE aktu_notification_cache SET is_latest = 0 WHERE is_latest = 1",
        )

        await execute(
            settings.FEEDBACK_DB,
            "INSERT INTO aktu_notification_cache "
            "(id, url, content, content_hash, fetched_at, is_latest) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                "openai",
                content,
                new_hash,
                datetime.utcnow().isoformat(),
                1,
            ),
        )

        logger.info("AKTU notifications UPDATED — new content cached")
        return {"status": "updated", "hash": new_hash, "chars": len(content)}

    async def get_cached(self) -> Optional[dict]:
        """Get cached notifications if available."""
        await self._ensure_table()

        row = await fetch_one(
            settings.FEEDBACK_DB,
            "SELECT content, fetched_at FROM aktu_notification_cache "
            "WHERE is_latest = 1 ORDER BY fetched_at DESC LIMIT 1",
        )

        if not row:
            return None

        fetched_at = datetime.fromisoformat(row["fetched_at"])
        age_hours = (datetime.utcnow() - fetched_at).total_seconds() / 3600

        return {
            "content": row["content"],
            "fetched_at": row["fetched_at"],
            "age_hours": round(age_hours, 1),
            "is_stale": age_hours > CACHE_VALIDITY_HOURS,
        }

    async def get_notifications(self, force_fetch: bool = False) -> str:
        """
        Main entry point. Priority order:
        1. Return cache if valid (< 7 hours) and not force
        2. Try OpenAI fetch
        3. Try direct website fetch (fallback)
        4. Return stale cache
        5. Return "no data available"
        """
        await self._ensure_table()

        # 1. Cache first (unless force)
        if not force_fetch:
            cached = await self.get_cached()
            if cached and not cached["is_stale"]:
                age = cached["age_hours"]
                return (
                    f"[From cache — last updated {age:.1f} hours ago]\n\n"
                    f"{cached['content'][:3000]}"
                )
        else:
            cached = await self.get_cached()

        # 2. Try OpenAI (primary source)
        content = await self.fetch_from_openai()
        if content:
            await self.update_cache(content)
            source = "OpenAI (live)" if force_fetch else "OpenAI"
            return f"[Fetched via {source}]\n\n{content[:3000]}"

        # 3. Try website (fallback — usually empty due to JS rendering)
        content = await self.fetch_from_website()
        if content:
            await self.update_cache(content)
            return f"[Fetched from AKTU website]\n\n{content[:3000]}"

        # 4. Stale cache
        if cached:
            age = cached["age_hours"]
            return (
                f"[From cache — {age:.1f} hours old, could not refresh]\n\n"
                f"{cached['content'][:3000]}"
            )

        # 5. Nothing available
        if not settings.OPENAI_ENABLED:
            return (
                "No AKTU notification data available. "
                "Enable OpenAI (set OPENAI_ENABLED=true in .env) to fetch live AKTU data."
            )
        return "Could not fetch AKTU notifications. Please try again later."

    @staticmethod
    def is_force_fetch_query(query: str) -> bool:
        """Check if user wants to force a live fetch."""
        q = query.lower()
        force_patterns = [
            "fetch", "refresh", "live", "right now", "directly from website",
            "update now", "get latest", "check now", "force", "real time",
            "fresh data", "bypass cache", "new data",
        ]
        return any(p in q for p in force_patterns)

    @staticmethod
    def is_notification_query(query: str) -> bool:
        """Check if query is about AKTU notifications."""
        q = query.lower()
        notification_keywords = [
            "notification", "circular", "notice", "announcement",
            "recent update", "latest update", "aktu update",
            "aktu news", "new circular", "recent circular",
            "aktu notification", "university notification",
        ]
        return any(kw in q for kw in notification_keywords)


# Singleton
aktu_service = AKTUNotificationService()

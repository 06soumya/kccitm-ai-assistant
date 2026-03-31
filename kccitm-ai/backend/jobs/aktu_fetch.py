"""
Scheduled AKTU notification fetch. Runs at midnight daily.
Uses OpenAI as primary source, website as fallback.
"""

import logging

logger = logging.getLogger(__name__)


async def run():
    """Fetch AKTU notifications and update cache."""
    try:
        from core.aktu_notifications import aktu_service

        logger.info("Starting scheduled AKTU notification fetch...")

        # Try OpenAI first (primary — website is JS-rendered)
        content = await aktu_service.fetch_from_openai()
        if content:
            result = await aktu_service.update_cache(content)
            logger.info("AKTU notifications updated via OpenAI: %s", result)
            return

        # Fallback to website
        content = await aktu_service.fetch_from_website()
        if content:
            result = await aktu_service.update_cache(content)
            logger.info("AKTU notifications updated via website: %s", result)
        else:
            logger.warning("AKTU scheduled fetch: no content from OpenAI or website")

    except Exception as e:
        logger.error("AKTU scheduled fetch failed: %s", e)

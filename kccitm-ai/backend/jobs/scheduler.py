"""
APScheduler configuration — 3 batch jobs for the adaptive intelligence layer.

  daily_healing    — 02:00 UTC — heal failed queries + collect training data
  daily_faq        — 03:00 UTC — generate FAQs from successful queries
  weekly_prompts   — Sunday 03:00 UTC — evolve prompts + evaluate A/B tests
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def setup_scheduler() -> None:
    """Configure all scheduled jobs. Called once at application startup."""

    scheduler.add_job(
        run_daily_healing,
        CronTrigger(hour=2, minute=0),
        id="daily_healing",
        name="Daily failed query healing + training data collection",
        replace_existing=True,
    )
    scheduler.add_job(
        run_daily_faq,
        CronTrigger(hour=3, minute=0),
        id="daily_faq",
        name="Daily FAQ generation from successful queries",
        replace_existing=True,
    )
    scheduler.add_job(
        run_weekly_prompts,
        CronTrigger(day_of_week="sun", hour=3, minute=0),
        id="weekly_prompts",
        name="Weekly prompt evolution and A/B test evaluation",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "Scheduler started: daily_healing (02:00), daily_faq (03:00), "
        "weekly_prompts (Sun 03:00)"
    )
    print("Scheduler started with 3 jobs: daily_healing (2AM), daily_faq (3AM), weekly_prompts (Sun 3AM)")


# ── Job wrappers (thin — delegate to jobs/ modules) ───────────────────────────

async def run_daily_healing() -> None:
    from jobs.daily_healing import run
    await run()


async def run_daily_faq() -> None:
    from jobs.daily_faq import run
    await run()


async def run_weekly_prompts() -> None:
    from jobs.weekly_prompts import run
    await run()

"""
STaR-SQL batch rationalization job.

Runs at 2:30 AM daily (after healing job at 2:00 AM).
Processes approved healing entries into rationalized training data.
"""

import logging

logger = logging.getLogger(__name__)


async def run_star_rationalization():
    """Batch process approved healing entries into training data."""
    try:
        from adaptive.star_sql import StarSQLTrainer
        from core.llm_client import OllamaClient

        star = StarSQLTrainer(OllamaClient())
        results = await star.batch_rationalize_from_healing()
        logger.info("STaR batch rationalization complete: %s", results)

        stats = await star.get_training_stats()
        logger.info(
            "Training progress: %d/%d (%.1f%%)",
            stats["total"], stats["target"], stats["progress_pct"],
        )

        if stats["ready_for_lora"]:
            logger.info("TARGET REACHED: 500+ training candidates. Ready for LoRA fine-tuning!")

    except Exception as e:
        logger.error("STaR batch job failed: %s", e)

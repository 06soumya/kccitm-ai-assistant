"""
Daily batch job: generate FAQ entries from clusters of successful queries.
"""

import logging
from datetime import datetime

from adaptive.faq_generator import FAQGenerator
from api.deps import get_llm, get_milvus

logger = logging.getLogger(__name__)


async def run() -> dict:
    start = datetime.utcnow().isoformat()
    print(f"\n[{start}] Starting daily FAQ generation...")

    llm   = get_llm()
    milvus = get_milvus()
    generator = FAQGenerator(llm, milvus)

    result = await generator.run_generation()

    print(f"  Clusters found: {result['clusters_found']}")
    print(f"  FAQs generated: {result['faqs_generated']}")
    print(f"  FAQs updated:   {result['faqs_updated']}")
    print(f"  Daily FAQ generation complete.")
    return result

"""
Weekly batch job: evaluate A/B tests and evolve prompts from failure patterns.
"""

import logging
from datetime import datetime

from adaptive.prompt_ab_tester import PromptABTester
from adaptive.prompt_evolver import PromptEvolver
from api.deps import get_llm

logger = logging.getLogger(__name__)


async def run() -> dict:
    start = datetime.utcnow().isoformat()
    print(f"\n[{start}] Starting weekly prompt evolution...")

    llm      = get_llm()
    evolver  = PromptEvolver(llm)
    ab_tester = PromptABTester()

    # 1. Evaluate existing A/B tests
    decisions = await ab_tester.evaluate_tests()
    for d in decisions:
        print(f"  A/B result: {d['prompt']}/{d['section']} — winner: {d['winner']}")

    # 2. Generate new proposals
    result = await evolver.run_evolution()
    print(f"  Clusters analyzed:    {result['clusters_found']}")
    print(f"  Proposals generated:  {result['proposals_generated']}")
    for p in result.get("proposals", []):
        print(f"  Proposal: {p.get('target_prompt')}/{p.get('target_section')} — {p.get('reasoning', '')[:80]}")

    print(f"  Weekly prompt evolution complete.")
    return {
        "ab_decisions": len(decisions),
        "clusters_analyzed": result["clusters_found"],
        "proposals_generated": result["proposals_generated"],
    }

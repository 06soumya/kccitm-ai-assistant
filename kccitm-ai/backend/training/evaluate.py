"""
Shadow evaluation — compare base model vs fine-tuned model on held-out queries.

Both models answer the same prompts; responses are logged side-by-side.
No user traffic is affected. Admin reviews evaluation_results.json and decides
whether to deploy the fine-tuned model.

Run:
    python -m training.evaluate --run v20260318_1430
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

from config import settings
from core.llm_client import OllamaClient

# ── Held-out evaluation queries (covers all route types) ─────────────────────

EVAL_QUERIES = [
    # SQL-type
    "top 5 students by SGPA in semester 1",
    "how many CSE students are there",
    "average SGPA in semester 4",
    "students with SGPA below 6 in semester 3",
    # RAG-type
    "tell me about a student's overall performance",
    "which students are struggling in programming",
    "describe the CSE batch performance trend",
    "who performed well in practicals",
    # Subject code
    "KCS503 results",
    "KAS101T grades",
    # Complex
    "compare semester 1 and semester 4 SGPA for CSE",
    "why did performance drop in later semesters",
]

_EVAL_SYSTEM = (
    "You are KCCITM AI Assistant. "
    "Answer questions about student academic data concisely."
)


async def evaluate(run_id: str, models_dir: str = "data/models") -> None:
    """Run shadow evaluation. Saves results to evaluation_results.json."""
    run_dir   = Path(models_dir) / run_id
    meta_path = run_dir / "training_meta.json"

    if not meta_path.exists():
        print(f"\033[91mTraining run '{run_id}' not found at {run_dir}\033[0m")
        return

    meta = json.loads(meta_path.read_text())
    finetuned_model = meta.get("ollama_model")
    base_model      = settings.OLLAMA_MODEL

    if not finetuned_model:
        print(f"\033[91mNo Ollama model registered for run {run_id}.\033[0m")
        print("Run merge_and_quantize first: python -m training.merge_and_quantize --run " + run_id)
        return

    print(f"\n{'='*60}")
    print(f"Shadow Evaluation: {run_id}")
    print(f"{'='*60}")
    print(f"  Base model    : {base_model}")
    print(f"  Fine-tuned    : {finetuned_model}")
    print(f"  Queries       : {len(EVAL_QUERIES)}")
    print(f"{'='*60}")

    base_llm = OllamaClient(model=base_model)
    ft_llm   = OllamaClient(model=finetuned_model)

    # Connectivity check
    base_health = await base_llm.health_check()
    if base_health.get("status") != "ok":
        print(f"\033[91mBase model ({base_model}) is not reachable.\033[0m")
        return

    ft_health = await ft_llm.health_check()
    available_models = [m.get("name", "") for m in ft_health.get("models", [])]
    if not any(finetuned_model in m for m in available_models):
        print(f"\033[93mNote: {finetuned_model} not listed — attempting anyway.\033[0m")

    results = []

    for i, query in enumerate(EVAL_QUERIES, 1):
        print(f"\n--- Query {i}/{len(EVAL_QUERIES)} ---")
        print(f"  Q: \"{query}\"")

        # Base model response
        t0 = time.time()
        try:
            base_resp = await base_llm.generate(
                prompt=query, system=_EVAL_SYSTEM, max_tokens=300
            )
            base_ms = (time.time() - t0) * 1000
        except Exception as exc:
            base_resp, base_ms = f"ERROR: {exc}", 0.0

        # Fine-tuned model response
        t0 = time.time()
        try:
            ft_resp = await ft_llm.generate(
                prompt=query, system=_EVAL_SYSTEM, max_tokens=300
            )
            ft_ms = (time.time() - t0) * 1000
        except Exception as exc:
            ft_resp, ft_ms = f"ERROR: {exc}", 0.0

        print(f"  Base ({base_ms:.0f}ms): {base_resp[:120]}...")
        print(f"  FT   ({ft_ms:.0f}ms):  {ft_resp[:120]}...")

        results.append({
            "query":         query,
            "base_response": base_resp,
            "base_time_ms":  round(base_ms, 1),
            "ft_response":   ft_resp,
            "ft_time_ms":    round(ft_ms, 1),
        })

    # Save results
    eval_path = run_dir / "evaluation_results.json"
    base_avg = sum(r["base_time_ms"] for r in results) / len(results)
    ft_avg   = sum(r["ft_time_ms"]   for r in results) / len(results)

    eval_output = {
        "run_id":          run_id,
        "base_model":      base_model,
        "finetuned_model": finetuned_model,
        "evaluated_at":    time.strftime("%Y-%m-%dT%H:%M:%S"),
        "summary": {
            "queries_tested": len(results),
            "base_avg_time_ms": round(base_avg, 1),
            "ft_avg_time_ms":   round(ft_avg, 1),
        },
        "results": results,
    }

    with open(eval_path, "w", encoding="utf-8") as f:
        json.dump(eval_output, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"Evaluation Summary")
    print(f"{'='*60}")
    print(f"  Queries tested   : {len(results)}")
    print(f"  Base avg time    : {base_avg:.0f} ms")
    print(f"  FT avg time      : {ft_avg:.0f} ms")
    print(f"  Results saved    : {eval_path}")
    print(f"\nTo deploy fine-tuned model:")
    print(f"  Update .env: OLLAMA_MODEL={finetuned_model}")
    print(f"  Or via API:  POST /api/admin/models/switch")
    print(f"\nTo rollback: OLLAMA_MODEL={base_model}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Shadow evaluation: base vs fine-tuned")
    parser.add_argument("--run",        required=True, help="Training run ID")
    parser.add_argument("--models-dir", default="data/models")
    args = parser.parse_args()
    asyncio.run(evaluate(args.run, args.models_dir))

"""
Admin routes — cache management, dashboard metrics, FAQs, prompts, training, batch jobs.
All endpoints require admin role.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.deps import get_cache
from api.middleware.auth import TokenData, require_admin
from config import settings
from core.cache import QueryCache
from db.sqlite_client import execute, fetch_all, fetch_one

router = APIRouter()


# ── Cache ─────────────────────────────────────────────────────────────────────

@router.post("/reindex")
async def reindex(admin: TokenData = Depends(require_admin)):
    return {
        "message": "Reindex is a manual process. Run these commands on the server:",
        "commands": [
            "cd backend",
            "python -m ingestion.etl",
            "python -m ingestion.embedder",
            "python -m ingestion.milvus_indexer",
            "python -m ingestion.init_prompts",
        ],
    }


@router.post("/cache/clear")
async def clear_cache(
    admin: TokenData = Depends(require_admin),
    cache: QueryCache = Depends(get_cache),
):
    await cache.clear()
    return {"message": "Cache cleared successfully"}


@router.get("/cache/stats")
async def cache_stats(
    admin: TokenData = Depends(require_admin),
    cache: QueryCache = Depends(get_cache),
):
    return await cache.get_stats()


@router.get("/dashboard/metrics")
async def dashboard_metrics(admin: TokenData = Depends(require_admin)):
    session_count = await fetch_one(settings.SESSION_DB, "SELECT COUNT(*) AS cnt FROM sessions")
    message_count = await fetch_one(settings.SESSION_DB, "SELECT COUNT(*) AS cnt FROM messages")
    user_count    = await fetch_one(settings.SESSION_DB, "SELECT COUNT(*) AS cnt FROM users")
    cache = get_cache()
    cache_data = await cache.get_stats()
    return {
        "sessions": session_count["cnt"] if session_count else 0,
        "messages": message_count["cnt"] if message_count else 0,
        "users":    user_count["cnt"]    if user_count    else 0,
        "cache":    cache_data,
    }


# ── FAQs ──────────────────────────────────────────────────────────────────────

@router.get("/faqs")
async def list_faqs(admin: TokenData = Depends(require_admin)):
    rows = await fetch_all(settings.PROMPTS_DB, "SELECT * FROM faq_entries ORDER BY hit_count DESC")
    return {"faqs": [dict(r) for r in (rows or [])]}


class FAQUpdateBody(BaseModel):
    question: str
    answer: str


@router.put("/faqs/{faq_id}")
async def update_faq(
    faq_id: str,
    body: FAQUpdateBody,
    admin: TokenData = Depends(require_admin),
):
    await execute(
        settings.PROMPTS_DB,
        """UPDATE faq_entries
           SET canonical_question = ?, answer = ?, admin_verified = 1,
               updated_at = datetime('now')
           WHERE id = ?""",
        (body.question, body.answer, faq_id),
    )
    return {"message": "FAQ updated"}


@router.delete("/faqs/{faq_id}")
async def retire_faq(faq_id: str, admin: TokenData = Depends(require_admin)):
    await execute(
        settings.PROMPTS_DB,
        "UPDATE faq_entries SET status = 'retired', updated_at = datetime('now') WHERE id = ?",
        (faq_id,),
    )
    return {"message": "FAQ retired"}


# ── Chunk analytics ───────────────────────────────────────────────────────────

@router.get("/chunks/health")
async def chunk_health(admin: TokenData = Depends(require_admin)):
    from adaptive.chunk_analyzer import ChunkAnalyzer
    return await ChunkAnalyzer().get_health_summary()


# ── Training data ─────────────────────────────────────────────────────────────

@router.get("/training/stats")
async def training_stats(admin: TokenData = Depends(require_admin)):
    from adaptive.training_data_manager import TrainingDataManager
    return await TrainingDataManager().get_stats()


@router.get("/training/candidates")
async def training_candidates(
    category: str = Query(None),
    admin: TokenData = Depends(require_admin),
):
    from adaptive.training_data_manager import TrainingDataManager
    candidates = await TrainingDataManager().get_candidates(category=category)
    return {"candidates": candidates}


@router.delete("/training/candidates/{candidate_id}")
async def exclude_training_candidate(
    candidate_id: str,
    admin: TokenData = Depends(require_admin),
):
    from adaptive.training_data_manager import TrainingDataManager
    await TrainingDataManager().exclude_candidate(candidate_id)
    return {"message": "Candidate excluded"}


# ── Prompts ───────────────────────────────────────────────────────────────────

@router.get("/prompts")
async def list_prompts(admin: TokenData = Depends(require_admin)):
    rows = await fetch_all(
        settings.PROMPTS_DB,
        "SELECT * FROM prompt_templates WHERE is_active = 1 ORDER BY prompt_name, section_name",
    )
    return {"prompts": [dict(r) for r in (rows or [])]}


@router.get("/prompts/proposals")
async def prompt_proposals(admin: TokenData = Depends(require_admin)):
    rows = await fetch_all(
        settings.PROMPTS_DB,
        "SELECT * FROM prompt_evolution_log WHERE approved_by = 'pending' ORDER BY created_at DESC",
    )
    return {"proposals": [dict(r) for r in (rows or [])]}


@router.post("/prompts/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: str, admin: TokenData = Depends(require_admin)):
    from adaptive.prompt_ab_tester import PromptABTester

    row = await fetch_one(
        settings.PROMPTS_DB,
        "SELECT * FROM prompt_evolution_log WHERE id = ?",
        (proposal_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Proposal not found")

    try:
        proposal = json.loads(row["change_diff"]) if row.get("change_diff") else {}
    except (TypeError, ValueError):
        proposal = {}

    ab_tester = PromptABTester()
    new_version = await ab_tester.create_new_version(
        prompt_name=row["prompt_name"],
        section_name=row["section_name"],
        content=proposal.get("content", ""),
        reason=row.get("change_reason", ""),
    )
    await execute(
        settings.PROMPTS_DB,
        "UPDATE prompt_evolution_log SET approved_by = ? WHERE id = ?",
        (admin.username, proposal_id),
    )
    return {"message": f"Approved. New version v{new_version} created for A/B testing."}


@router.post("/prompts/{prompt_name}/{section_name}/rollback")
async def rollback_prompt(
    prompt_name: str,
    section_name: str,
    admin: TokenData = Depends(require_admin),
):
    from adaptive.prompt_ab_tester import PromptABTester
    success = await PromptABTester().rollback_prompt(prompt_name, section_name)
    return {"message": "Rolled back" if success else "Nothing to rollback"}


# ── Batch job triggers ────────────────────────────────────────────────────────

@router.post("/jobs/healing/run")
async def trigger_healing(admin: TokenData = Depends(require_admin)):
    from jobs.daily_healing import run
    result = await run()
    return {"message": "Healing job completed", **result}


@router.post("/jobs/faq/run")
async def trigger_faq(admin: TokenData = Depends(require_admin)):
    from jobs.daily_faq import run
    result = await run()
    return {"message": "FAQ generation completed", **result}


@router.post("/jobs/prompts/run")
async def trigger_prompts(admin: TokenData = Depends(require_admin)):
    from jobs.weekly_prompts import run
    result = await run()
    return {"message": "Prompt evolution completed", **result}


@router.get("/rechunking/proposals")
async def rechunking_proposals(admin: TokenData = Depends(require_admin)):
    from adaptive.rechunker import Rechunker
    from api.deps import get_llm, get_milvus
    proposals = await Rechunker(get_llm(), get_milvus()).generate_proposals()
    return {"proposals": proposals}


# ── Model management (Phase 10) ───────────────────────────────────────────────

@router.get("/models")
async def list_models(admin: TokenData = Depends(require_admin)):
    """List all available model versions (base + fine-tuned)."""
    from training.model_manager import ModelManager
    manager = ModelManager()
    return {"models": manager.list_versions(), "active": manager.get_active_model()}


class ModelSwitchBody(BaseModel):
    model_name: str


@router.post("/models/switch")
async def switch_model(
    body: ModelSwitchBody,
    admin: TokenData = Depends(require_admin),
):
    """Switch the active LLM model. Server restart required for change to take effect."""
    from training.model_manager import ModelManager
    manager = ModelManager()
    success = manager.switch_model(body.model_name)
    if success:
        return {
            "message": f"Model switched to {body.model_name}. Restart server to apply.",
            "success": True,
        }
    return {"message": "Model not found or switch failed", "success": False}


@router.get("/models/{run_id}/evaluation")
async def get_model_evaluation(run_id: str, admin: TokenData = Depends(require_admin)):
    """Load evaluation results for a training run."""
    from training.model_manager import ModelManager
    result = ModelManager().get_evaluation(run_id)
    if not result:
        raise HTTPException(status_code=404, detail="Evaluation results not found")
    return result


@router.post("/training/export")
async def export_training_data(
    category: str = Query(None),
    min_score: float = Query(0.8),
    admin: TokenData = Depends(require_admin),
):
    """Export accumulated training candidates as JSONL files."""
    from training.export_data import export
    result = await export(category=category, min_score=min_score)
    return result


@router.post("/training/run")
async def training_instructions(admin: TokenData = Depends(require_admin)):
    """Return manual training instructions (training is an offline process)."""
    from adaptive.training_data_manager import TrainingDataManager
    stats = await TrainingDataManager().get_stats()
    return {
        "message": "LoRA training is a manual offline process. Run these commands on the server:",
        "commands": [
            "cd backend",
            "python -m training.export_data",
            "python -m training.train_lora --data data/training/combined.jsonl --epochs 3",
            "python -m training.merge_and_quantize --run <RUN_ID>",
            "python -m training.evaluate --run <RUN_ID>",
        ],
        "current_training_stats": stats,
        "ready_for_lora": stats["ready_for_lora"],
        "note": "500+ high-quality candidates recommended before training.",
    }

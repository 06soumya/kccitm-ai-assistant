"""
Eval routes — admin-only.

GET  /api/admin/eval/queries        list the queries.yaml content
POST /api/admin/eval/run            kick off a full eval run in the background
GET  /api/admin/eval/runs           list past runs (most recent first)
GET  /api/admin/eval/runs/{run_id}  detail view (per-query results)
GET  /api/admin/eval/runs/latest    shortcut for the latest run
"""

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_orchestrator
from api.middleware.auth import TokenData, require_admin
from core.orchestrator import Orchestrator
from eval.runner import (
    get_latest_run,
    get_queries,
    get_run,
    list_runs,
    load_queries,
    run_eval,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/eval/queries")
async def eval_queries(_: TokenData = Depends(require_admin)):
    """Return the parsed queries.yaml — used by the admin UI to preview the set."""
    data = load_queries()
    queries = data.get("queries", [])
    return {
        "metadata": data.get("metadata", {}),
        "count": len(queries),
        "queries": queries,
    }


@router.post("/eval/run")
async def eval_run(
    _: TokenData = Depends(require_admin),
    orchestrator: Orchestrator = Depends(get_orchestrator),
):
    """
    Kick off a full eval run. Returns immediately with run_id; the run
    continues in the background and the client polls runs/{run_id} for
    progress. Local 7B LLM means a full 50-query run takes ~30-90 minutes.
    """
    run_id = f"run_{uuid.uuid4().hex[:8]}"

    async def _bg():
        try:
            await run_eval(orchestrator, run_id=run_id)
            logger.info("Eval run %s finished", run_id)
        except Exception as exc:
            logger.exception("Eval run %s failed: %s", run_id, exc)

    asyncio.create_task(_bg())
    return {"run_id": run_id, "total": len(get_queries())}


@router.get("/eval/runs")
async def eval_list_runs(
    limit: int = 20,
    _: TokenData = Depends(require_admin),
):
    runs = await list_runs(limit=limit)
    return {"runs": runs}


@router.get("/eval/runs/latest")
async def eval_latest_run(_: TokenData = Depends(require_admin)):
    run = await get_latest_run()
    if not run:
        raise HTTPException(status_code=404, detail="No eval runs yet")
    return run


@router.get("/eval/runs/{run_id}")
async def eval_run_detail(
    run_id: str,
    _: TokenData = Depends(require_admin),
):
    run = await get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return run

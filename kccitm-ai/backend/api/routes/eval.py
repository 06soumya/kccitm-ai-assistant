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
    clear_cache: bool = True,
    _: TokenData = Depends(require_admin),
    orchestrator: Orchestrator = Depends(get_orchestrator),
):
    """
    Kick off a full eval run. Returns immediately with run_id; the run
    continues in the background and the client polls runs/{run_id} for
    progress. Local 7B LLM means a full 50-query run takes ~30-90 minutes.

    By default the orchestrator's semantic cache is cleared before the
    run so we measure real routing decisions, not stale hits. Pass
    `?clear_cache=false` to evaluate cached behavior instead.

    Refuses to start a new run if one is already in progress — concurrent
    runs share the same orchestrator + LLM, so they slow each other down
    and pollute each other's pass/fail signal.
    """
    # Guard: refuse to start a new run if one is already in progress.
    existing = await list_runs(limit=5)
    in_flight = next((r for r in existing if r.get("status") == "running"), None)
    if in_flight:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Run {in_flight['id']} is already in progress "
                f"({in_flight['completed']}/{in_flight['total']}). "
                f"Wait for it to finish or abort it before starting a new one."
            ),
        )

    run_id = f"run_{uuid.uuid4().hex[:8]}"

    async def _bg():
        try:
            await run_eval(orchestrator, run_id=run_id, clear_cache=clear_cache)
            logger.info("Eval run %s finished", run_id)
        except Exception as exc:
            logger.exception("Eval run %s failed: %s", run_id, exc)

    asyncio.create_task(_bg())
    return {"run_id": run_id, "total": len(get_queries()), "clear_cache": clear_cache}


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


@router.post("/eval/runs/{run_id}/pause")
async def eval_pause(
    run_id: str,
    _: TokenData = Depends(require_admin),
):
    """
    Cooperative pause. Flips status to 'paused'; the runner sees it before
    the next query and exits gracefully. The currently in-flight query
    finishes and gets recorded. Resume picks up from there.
    """
    from db.sqlite_client import execute
    from eval.runner import EVAL_DB, get_run

    run = await get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if run["status"] != "running":
        raise HTTPException(
            status_code=409,
            detail=f"Run is {run['status']}, can only pause a running run",
        )
    await execute(
        str(EVAL_DB),
        "UPDATE eval_runs SET status='paused' WHERE id = ? AND status='running'",
        (run_id,),
    )
    return {"run_id": run_id, "status": "paused"}


@router.post("/eval/runs/{run_id}/resume")
async def eval_resume(
    run_id: str,
    _: TokenData = Depends(require_admin),
    orchestrator: Orchestrator = Depends(get_orchestrator),
):
    """
    Resume a paused run from the next un-processed query. Same run_id is
    reused; already-completed results stay. Refuses to resume if another
    run is currently in progress.
    """
    from eval.runner import EVAL_DB, get_run, run_eval as run_eval_fn
    from db.sqlite_client import fetch_one as _fetch_one

    run = await get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if run["status"] not in ("paused", "aborted"):
        raise HTTPException(
            status_code=409,
            detail=f"Run is {run['status']}; only paused or aborted runs can be resumed",
        )

    # Guard: don't start a second concurrent run.
    other = await _fetch_one(
        str(EVAL_DB),
        "SELECT id FROM eval_runs WHERE status='running' LIMIT 1",
    )
    if other:
        raise HTTPException(
            status_code=409,
            detail=f"Run {other['id']} is already in progress",
        )

    async def _bg():
        try:
            await run_eval_fn(
                orchestrator,
                run_id=run_id,
                clear_cache=False,
                resume=True,
            )
            logger.info("Eval run %s resumed-finished", run_id)
        except Exception as exc:
            logger.exception("Eval resume %s failed: %s", run_id, exc)

    asyncio.create_task(_bg())
    return {"run_id": run_id, "status": "resuming"}


@router.post("/eval/runs/{run_id}/abort")
async def eval_abort(
    run_id: str,
    _: TokenData = Depends(require_admin),
):
    """
    Hard cancel. Like pause but the run can't be resumed without
    deliberately calling /resume. Used for runs we no longer want.
    """
    from db.sqlite_client import execute
    from eval.runner import EVAL_DB, get_run

    run = await get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if run["status"] in ("finished", "aborted"):
        return {"run_id": run_id, "status": run["status"], "note": "already terminal"}
    import time as _t
    await execute(
        str(EVAL_DB),
        "UPDATE eval_runs SET status='aborted', finished_at=? WHERE id = ?",
        (_t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime()), run_id),
    )
    return {"run_id": run_id, "status": "aborted"}

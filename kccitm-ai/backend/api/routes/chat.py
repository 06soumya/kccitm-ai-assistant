"""
Chat routes: POST /chat with optional SSE streaming.

Non-streaming: Returns complete JSON response.
Streaming:     Returns SSE token stream (text/event-stream).

SSE event types:
  {"type": "status", "message": "..."}  — thinking indicator
  {"type": "token",  "content": "..."}  — individual LLM token
  {"type": "done",   "total_time_ms": N, "session_id": "..."}  — stream complete
  {"type": "error",  "message": "..."}  — failure
"""
import json
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.deps import get_orchestrator, get_session_manager
from api.middleware.auth import TokenData, get_current_user
from core.orchestrator import Orchestrator
from core.session_manager import SessionManager

router = APIRouter()


# ── Models ────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    stream: bool = False


class ChatResponse(BaseModel):
    response: str
    session_id: str
    route_used: str
    total_time_ms: float
    metadata: dict = {}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/chat")
async def chat(
    req: ChatRequest,
    current_user: TokenData = Depends(get_current_user),
    orchestrator: Orchestrator = Depends(get_orchestrator),
    session_mgr: SessionManager = Depends(get_session_manager),
):
    """
    Main chat endpoint.

    Automatically creates a new session when session_id is omitted.
    Verifies session ownership before processing.
    """
    # Resolve or create session
    session_id = req.session_id
    if not session_id:
        session = await session_mgr.create_session(current_user.user_id)
        session_id = session.id
    else:
        session = await session_mgr.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.user_id != current_user.user_id:
            raise HTTPException(status_code=403, detail="Session does not belong to you")

    # Admin commands — handle before streaming/non-streaming split
    _q = req.message.lower().strip()
    if _q in ("clear cache", "clear the cache", "reset cache", "flush cache"):
        from api.deps import get_cache
        cache = get_cache()
        await cache.clear()
        return ChatResponse(
            response="Cache cleared successfully.",
            session_id=session_id,
            route_used="ADMIN_CMD",
            total_time_ms=0,
            metadata={},
        )

    if req.stream:
        return StreamingResponse(
            _stream_via_process_query(req.message, session_id, orchestrator, current_user.user_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Non-streaming path
    result = await orchestrator.process_query(
        query=req.message,
        session_id=session_id,
        user_id=current_user.user_id,
    )

    if not result.success:
        raise HTTPException(status_code=500, detail=result.error)

    return ChatResponse(
        response=result.response,
        session_id=session_id,
        route_used=result.route_used,
        total_time_ms=result.total_time_ms,
        metadata=result.metadata,
    )


# ── SSE stream generator (Option C: full pipeline then stream text) ──────────

async def _stream_via_process_query(
    query: str,
    session_id: str,
    orchestrator: Orchestrator,
    user_id: str,
):
    """
    Run the full process_query() pipeline (with sanity checks, OpenAI fallback,
    etc.), then stream the completed response text to the frontend for the
    typing animation effect.

    This gives the frontend the same accuracy as non-streaming API calls.
    """
    import asyncio
    start_time = time.time()

    try:
        yield f"data: {json.dumps({'type': 'status', 'message': 'Analyzing your question...'})}\n\n"

        # Run full pipeline (non-streaming) — gets all corrections
        result = await orchestrator.process_query(
            query=query,
            session_id=session_id,
            user_id=user_id,
        )

        if not result.success:
            yield f"data: {json.dumps({'type': 'error', 'message': result.error or 'Query failed'})}\n\n"
            return

        # Stream the completed response text in chunks for typing effect
        response_text = result.response or ""
        chunk_size = 8  # characters per chunk — fast but visible typing
        for i in range(0, len(response_text), chunk_size):
            chunk = response_text[i:i + chunk_size]
            yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"
            await asyncio.sleep(0.01)  # tiny delay for typing effect

        elapsed = round((time.time() - start_time) * 1000)
        done_payload: dict = {
            'type': 'done',
            'total_time_ms': elapsed,
            'session_id': session_id,
            'route_used': result.route_used,
        }
        if result.metadata.get("chart_data"):
            done_payload['chart_data'] = result.metadata["chart_data"]
        yield f"data: {json.dumps(done_payload)}\n\n"

    except Exception as exc:
        yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"


# ── Legacy SSE stream generator (kept for reference) ─────────────────────────

async def _stream_response(
    query: str,
    session_id: str,
    orchestrator: Orchestrator,
):
    """
    Async generator that yields SSE-formatted events.

    Format: data: {JSON}\n\n  (two trailing newlines required by SSE spec)
    """
    start_time = time.time()

    try:
        yield f"data: {json.dumps({'type': 'status', 'message': 'Analyzing your question...'})}\n\n"

        async for token in orchestrator.process_query_stream(
            query, session_id=session_id
        ):
            yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"

        elapsed = round((time.time() - start_time) * 1000)
        yield f"data: {json.dumps({'type': 'done', 'total_time_ms': elapsed, 'session_id': session_id})}\n\n"

    except Exception as exc:
        yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

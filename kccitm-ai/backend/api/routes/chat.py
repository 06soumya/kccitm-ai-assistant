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

    if req.stream:
        return StreamingResponse(
            _stream_response(req.message, session_id, orchestrator),
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


# ── SSE stream generator ──────────────────────────────────────────────────────

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

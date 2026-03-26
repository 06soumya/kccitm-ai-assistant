"""
Session management routes: list, get, create, delete.
All endpoints enforce session ownership — users can only access their own sessions.
"""
from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_session_manager
from api.middleware.auth import TokenData, get_current_user
from core.session_manager import SessionManager

router = APIRouter()


@router.get("")
async def list_sessions(
    current_user: TokenData = Depends(get_current_user),
    session_mgr: SessionManager = Depends(get_session_manager),
):
    """List all sessions for the current user (most recent first)."""
    sessions = await session_mgr.list_sessions(current_user.user_id)
    return {"sessions": sessions}


@router.post("")
async def create_session(
    current_user: TokenData = Depends(get_current_user),
    session_mgr: SessionManager = Depends(get_session_manager),
):
    """Create a new empty session."""
    session = await session_mgr.create_session(current_user.user_id)
    return {"session_id": session.id, "created_at": session.created_at}


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    current_user: TokenData = Depends(get_current_user),
    session_mgr: SessionManager = Depends(get_session_manager),
):
    """Get a session with its full message history."""
    session = await session_mgr.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Not your session")

    return {
        "id": session.id,
        "title": session.title,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "messages": [
            {
                "id": msg.id,
                "role": msg.role,
                "content": msg.content,
                "metadata": msg.metadata,
                "created_at": msg.created_at,
            }
            for msg in session.messages
        ],
    }


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    current_user: TokenData = Depends(get_current_user),
    session_mgr: SessionManager = Depends(get_session_manager),
):
    """Delete a session and all its messages."""
    session = await session_mgr.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Not your session")

    await session_mgr.delete_session(session_id)
    return {"message": "Session deleted"}

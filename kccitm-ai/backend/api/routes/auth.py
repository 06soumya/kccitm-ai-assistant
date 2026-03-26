"""
Authentication routes: login, register (admin only), /me, list users.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.middleware.auth import (
    TokenData,
    create_access_token,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)
from config import settings
from db.sqlite_client import execute, fetch_all, fetch_one

router = APIRouter()


# ── Request / Response models ─────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    username: str
    role: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    role: str = "faculty"  # "admin" or "faculty"


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    """Authenticate user and return a JWT access token."""
    user = await fetch_one(
        settings.SESSION_DB,
        "SELECT * FROM users WHERE username = ?",
        (req.username,),
    )
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = create_access_token({
        "user_id": user["id"],
        "username": user["username"],
        "role": user["role"],
    })

    return LoginResponse(
        access_token=token,
        user_id=user["id"],
        username=user["username"],
        role=user["role"],
    )


@router.post("/register")
async def register(
    req: RegisterRequest,
    admin: TokenData = Depends(require_admin),
):
    """Register a new user. Admin only."""
    if req.role not in ("admin", "faculty"):
        raise HTTPException(status_code=400, detail="role must be 'admin' or 'faculty'")

    existing = await fetch_one(
        settings.SESSION_DB,
        "SELECT id FROM users WHERE username = ?",
        (req.username,),
    )
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    user_id = str(uuid.uuid4())
    await execute(
        settings.SESSION_DB,
        "INSERT INTO users (id, username, password_hash, role) VALUES (?, ?, ?, ?)",
        (user_id, req.username, hash_password(req.password), req.role),
    )

    return {"message": f"User '{req.username}' created", "user_id": user_id, "role": req.role}


@router.get("/me")
async def get_me(current_user: TokenData = Depends(get_current_user)):
    """Return current authenticated user's info."""
    return {
        "user_id": current_user.user_id,
        "username": current_user.username,
        "role": current_user.role,
    }


@router.get("/users")
async def list_users(admin: TokenData = Depends(require_admin)):
    """List all users. Admin only."""
    users = await fetch_all(
        settings.SESSION_DB,
        "SELECT id, username, role, created_at FROM users ORDER BY created_at DESC",
    )
    return {"users": [dict(u) for u in users]}

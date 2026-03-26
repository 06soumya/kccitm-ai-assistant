"""
JWT authentication middleware and utilities for KCCITM AI Assistant.

Provides:
- create_access_token() — sign a JWT
- verify_token()        — decode and validate a JWT
- get_current_user()    — FastAPI dependency for any authenticated route
- require_admin()       — FastAPI dependency for admin-only routes
- hash_password() / verify_password() — bcrypt helpers
"""
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from config import settings

# Password hashing (bcrypt)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Bearer token extractor
security = HTTPBearer()


# ── Models ────────────────────────────────────────────────────────────────────

class TokenData(BaseModel):
    user_id: str
    username: str
    role: str  # "admin" or "faculty"


# ── Token helpers ─────────────────────────────────────────────────────────────

def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
    """Create a signed JWT access token."""
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(hours=settings.JWT_EXPIRY_HOURS)
    )
    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def verify_token(token: str) -> TokenData:
    """Decode and validate a JWT. Raises HTTP 401 on failure."""
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        user_id: str = payload.get("user_id")
        username: str = payload.get("username")
        role: str = payload.get("role", "faculty")
        if not user_id or not username:
            raise HTTPException(status_code=401, detail="Invalid token payload")
        return TokenData(user_id=user_id, username=username, role=role)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=f"Token verification failed: {exc}")


# ── FastAPI dependencies ──────────────────────────────────────────────────────

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> TokenData:
    """
    Extract and verify the JWT from the Authorization header.

    Usage:
        @router.get("/endpoint")
        async def endpoint(current_user: TokenData = Depends(get_current_user)):
            ...
    """
    return verify_token(credentials.credentials)


async def require_admin(
    current_user: TokenData = Depends(get_current_user),
) -> TokenData:
    """Require admin role. Raises HTTP 403 for non-admin users."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

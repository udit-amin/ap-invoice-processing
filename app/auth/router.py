"""Auth routes: POST /auth/login and GET /auth/me."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app import config
from app.auth import service
from app.auth.dependencies import CurrentUser, get_current_user

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/auth/login")
def login(body: LoginRequest):
    """Exchange email + password for a bearer JWT (1-hour expiry)."""
    user = service.authenticate(body.email, body.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    token = service.create_access_token(user)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": config.JWT_EXPIRE_SECONDS,
        "user": {"name": user["name"], "role": user["role"]},
    }


@router.get("/auth/me")
def me(user: CurrentUser = Depends(get_current_user)):
    """Return the current user — lets the UI bootstrap its session from a token."""
    return {
        "user_id": user.user_id,
        "email": user.email,
        "role": user.role,
        "name": user.name,
    }

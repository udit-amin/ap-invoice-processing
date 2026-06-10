"""FastAPI auth dependencies: current-user extraction and role guards.

`get_current_user` validates the Bearer token and injects a CurrentUser.
`require_role(...)` builds a dependency that 403s when the caller's role is not
permitted — the permission boundary is visible to the UI (403, never 404).
"""
from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.auth import service

# auto_error=False so we raise our own 401 with a clear message rather than the
# framework default; tokenUrl drives the Swagger "Authorize" button only.
_bearer = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


@dataclass
class CurrentUser:
    user_id: str
    email: str
    role: str
    name: str


def get_current_user(token: str | None = Depends(_bearer)) -> CurrentUser:
    """Validate the Bearer token and return the acting user. 401 on any failure."""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = service.decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return CurrentUser(
        user_id=payload.get("sub"),
        email=payload.get("email"),
        role=payload.get("role"),
        name=payload.get("name"),
    )


def require_role(*roles: str):
    """Build a dependency that allows only the given role(s); 403 otherwise."""

    def _guard(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role: {', '.join(roles)}",
            )
        return user

    return _guard

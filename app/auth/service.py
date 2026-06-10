"""Auth service: password hashing, JWT issuing/decoding, authentication.

Pure-ish helpers around the users table. JWTs are session-less HS256 tokens
signed with config.get_jwt_secret(); the payload carries everything a route
guard needs (sub, email, role, name) so verification needs no DB round-trip.
"""
from __future__ import annotations

import time
from typing import Any

import jwt
from passlib.context import CryptContext

from app import config
from app.users import models

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Return a bcrypt hash for a plaintext password."""
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time check of a plaintext password against its bcrypt hash."""
    return _pwd_context.verify(password, password_hash)


def create_access_token(user: dict[str, Any]) -> str:
    """Issue an HS256 JWT for a user row. Expires in config.JWT_EXPIRE_SECONDS."""
    now = int(time.time())
    payload = {
        "sub": str(user["user_id"]),
        "email": user["email"],
        "role": user["role"],
        "name": user["name"],
        "iat": now,
        "exp": now + config.JWT_EXPIRE_SECONDS,
    }
    return jwt.encode(payload, config.get_jwt_secret(), algorithm=config.JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """Decode + verify a JWT. Raises jwt.PyJWTError (incl. ExpiredSignatureError)."""
    return jwt.decode(token, config.get_jwt_secret(), algorithms=[config.JWT_ALGORITHM])


def authenticate(email: str, password: str) -> dict[str, Any] | None:
    """Return the user row on valid credentials (and stamp last_login), else None."""
    user = models.get_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        return None
    models.update_last_login(user["user_id"])
    return user

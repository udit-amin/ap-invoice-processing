"""Auth tests: JWT issue/verify (pure) and login/me against the DB.

Login tests need Postgres (real user lookup) and skip cleanly when it is down,
mirroring the requires_db pattern; the pure token tests always run.
"""
from __future__ import annotations

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app import config
from app.auth import service
from app.main import app


def _db_available() -> bool:
    try:
        from app.db.connection import cursor
        with cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _db_available(), reason="Postgres not reachable — DB tests skipped.",
)

# (email, password, role, name) — the four seed users (handover §2.2).
DEMO_USERS = [
    ("priya@acmecorp.com",  "demo-clerk-1", "clerk",   "Priya Nair"),
    ("rahul@acmecorp.com",  "demo-clerk-2", "clerk",   "Rahul Sharma"),
    ("anjali@acmecorp.com", "demo-mgr-1",   "manager", "Anjali Mehta"),
    ("vikram@acmecorp.com", "demo-mgr-2",   "manager", "Vikram Iyer"),
]


@pytest.fixture(scope="module", autouse=True)
def _seed_users():
    """Ensure schema + demo users exist when a DB is available (no-op otherwise)."""
    if _db_available():
        from app.db.connection import apply_schema
        from app.users.seed import seed_users
        apply_schema()
        seed_users()
    yield


@pytest.fixture
def client():
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Pure JWT tests (no DB)
# --------------------------------------------------------------------------- #
def test_token_roundtrip_carries_claims():
    user = {"user_id": "u-1", "email": "a@b.c", "role": "clerk", "name": "Ada"}
    payload = service.decode_token(service.create_access_token(user))
    assert payload["sub"] == "u-1"
    assert payload["role"] == "clerk"
    assert payload["name"] == "Ada"
    assert payload["exp"] - payload["iat"] == config.JWT_EXPIRE_SECONDS


def test_expired_token_is_rejected():
    now = int(time.time())
    stale = jwt.encode(
        {"sub": "u", "email": "a@b.c", "role": "clerk", "name": "A",
         "iat": now - 10, "exp": now - 1},
        config.get_jwt_secret(), algorithm=config.JWT_ALGORITHM,
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        service.decode_token(stale)


def test_tampered_token_is_rejected():
    with pytest.raises(jwt.PyJWTError):
        service.decode_token("not.a.valid.jwt")


def test_password_hash_roundtrip():
    h = service.hash_password("s3cret")
    assert h != "s3cret"
    assert service.verify_password("s3cret", h)
    assert not service.verify_password("wrong", h)


# --------------------------------------------------------------------------- #
# Login / me (require DB)
# --------------------------------------------------------------------------- #
@requires_db
@pytest.mark.parametrize("email,password,role,name", DEMO_USERS)
def test_login_returns_valid_jwt(client, email, password, role, name):
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == config.JWT_EXPIRE_SECONDS
    assert body["user"] == {"name": name, "role": role}
    payload = service.decode_token(body["access_token"])
    assert payload["email"] == email and payload["role"] == role and payload["name"] == name


@requires_db
def test_login_bad_password_401(client):
    r = client.post("/auth/login", json={"email": "priya@acmecorp.com", "password": "nope"})
    assert r.status_code == 401


@requires_db
def test_login_unknown_user_401(client):
    r = client.post("/auth/login", json={"email": "ghost@acmecorp.com", "password": "x"})
    assert r.status_code == 401


@requires_db
def test_me_returns_current_user(client):
    tok = client.post(
        "/auth/login", json={"email": "anjali@acmecorp.com", "password": "demo-mgr-1"},
    ).json()["access_token"]
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["name"] == "Anjali Mehta"
    assert r.json()["role"] == "manager"


def test_me_without_token_401(client):
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer bad"}).status_code == 401

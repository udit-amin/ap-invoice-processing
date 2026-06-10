"""Raw-SQL helpers for the ``users`` table.

No ORM — consistent with the rest of the codebase (psycopg3 + raw SQL). Users
are global (not tenant-scoped) for v3, matching the handover schema.
"""
from __future__ import annotations

from typing import Any

from app.db.connection import cursor


def get_user_by_email(email: str) -> dict[str, Any] | None:
    """Return the full user row (incl. password_hash) for authentication, or None."""
    with cursor() as cur:
        cur.execute(
            "SELECT user_id, email, name, role, password_hash "
            "FROM users WHERE email = %s",
            (email,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "user_id": str(row[0]),
        "email": row[1],
        "name": row[2],
        "role": row[3],
        "password_hash": row[4],
    }


def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    """Return a user (without password_hash) by id, or None."""
    with cursor() as cur:
        cur.execute(
            "SELECT user_id, email, name, role FROM users WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"user_id": str(row[0]), "email": row[1], "name": row[2], "role": row[3]}


def update_last_login(user_id: str) -> None:
    """Stamp last_login = now() for a user."""
    with cursor() as cur:
        cur.execute("UPDATE users SET last_login = now() WHERE user_id = %s", (user_id,))


def upsert_user(email: str, name: str, role: str, password_hash: str) -> None:
    """Idempotent insert-or-update keyed by email (used by the demo seed)."""
    with cursor() as cur:
        cur.execute(
            """INSERT INTO users (email, name, role, password_hash)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (email) DO UPDATE SET
                   name          = EXCLUDED.name,
                   role          = EXCLUDED.role,
                   password_hash = EXCLUDED.password_hash""",
            (email, name, role, password_hash),
        )

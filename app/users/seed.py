"""Seed the four demo users (two clerks, two managers).

Idempotent — upserts by email, so repeated startups never duplicate. Passwords
are the demo credentials from the handover; they are bcrypt-hashed before write.

Run with:  python -m app.users.seed
"""
from __future__ import annotations

from app.auth import service
from app.db.connection import apply_schema
from app.users import models

# (name, email, role, password) — demo credentials (handover §2.2).
DEMO_USERS = [
    ("Priya Nair",   "priya@acmecorp.com",  "clerk",   "demo-clerk-1"),
    ("Rahul Sharma", "rahul@acmecorp.com",  "clerk",   "demo-clerk-2"),
    ("Anjali Mehta", "anjali@acmecorp.com", "manager", "demo-mgr-1"),
    ("Vikram Iyer",  "vikram@acmecorp.com", "manager", "demo-mgr-2"),
]


def seed_users() -> None:
    """Idempotent upsert of the four demo users (by email)."""
    for name, email, role, password in DEMO_USERS:
        models.upsert_user(
            email=email,
            name=name,
            role=role,
            password_hash=service.hash_password(password),
        )


if __name__ == "__main__":
    apply_schema()
    seed_users()
    print(f"Seeded {len(DEMO_USERS)} users "
          f"({sum(u[2] == 'clerk' for u in DEMO_USERS)} clerks, "
          f"{sum(u[2] == 'manager' for u in DEMO_USERS)} managers)")

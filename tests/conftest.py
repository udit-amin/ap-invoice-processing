"""Shared test fixtures/helpers.

`auth_header` mints a valid bearer token without a DB round-trip: the route
guards (get_current_user / require_role) read role/name/sub straight from the
verified JWT payload, so a synthetic user is enough to exercise authorization in
isolation. Tests that need a *real* user (e.g. login) seed and authenticate.
"""
from __future__ import annotations

import pytest

from app.auth import service


def make_token(
    role: str = "clerk",
    *,
    name: str | None = None,
    email: str | None = None,
    user_id: str = "00000000-0000-0000-0000-000000000000",
) -> str:
    return service.create_access_token(
        {
            "user_id": user_id,
            "email": email or f"{role}@example.com",
            "role": role,
            "name": name or f"Test {role.title()}",
        }
    )


@pytest.fixture
def auth_header():
    """Return a helper: auth_header(role) -> {'Authorization': 'Bearer ...'}."""
    def _header(role: str = "clerk") -> dict[str, str]:
        return {"Authorization": f"Bearer {make_token(role)}"}

    return _header

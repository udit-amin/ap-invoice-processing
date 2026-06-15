"""Admin reset endpoint — manager-only and env-gated (skip-if-DB-down)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import service
from app.main import app

MGR = "dddddddd-0000-0000-0000-000000000004"
CLERK = "eeeeeeee-0000-0000-0000-000000000005"


def _db_available() -> bool:
    try:
        from app.db.connection import cursor
        with cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(not _db_available(),
                                 reason="Postgres not reachable — DB tests skipped.")


@pytest.fixture
def client():
    return TestClient(app)


def _hdr(role: str, user_id: str) -> dict[str, str]:
    token = service.create_access_token(
        {"user_id": user_id, "email": f"{user_id}@x.com", "role": role, "name": role})
    return {"Authorization": f"Bearer {token}"}


def test_reset_forbidden_for_clerk(client, monkeypatch):
    # Role guard runs before the env gate, so a clerk is 403 regardless.
    monkeypatch.setenv("ALLOW_DEMO_RESET", "true")
    assert client.post("/admin/reset-demo", headers=_hdr("clerk", CLERK)).status_code == 403


def test_reset_disabled_without_env(client, monkeypatch):
    monkeypatch.setenv("ALLOW_DEMO_RESET", "false")
    r = client.post("/admin/reset-demo", headers=_hdr("manager", MGR))
    assert r.status_code == 403
    assert "disabled" in r.json()["detail"].lower()


@requires_db
def test_reset_seeds_six_three_two_when_enabled(client, monkeypatch):
    monkeypatch.setenv("ALLOW_DEMO_RESET", "true")
    body = client.post("/admin/reset-demo", headers=_hdr("manager", MGR)).json()
    assert body["runs"] == 11
    assert body["tally"] == {"APPROVE": 6, "FLAG": 3, "REJECT": 2}
    # The DB now holds exactly those 11 processed runs.
    summary = client.get("/dashboard/summary", headers=_hdr("manager", MGR)).json()
    assert summary["total_runs"] == 11

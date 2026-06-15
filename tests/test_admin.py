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
def test_reset_clears_to_clean_slate_when_enabled(client, monkeypatch):
    monkeypatch.setenv("ALLOW_DEMO_RESET", "true")
    # Seed a run, then confirm reset wipes operational data back to empty (the demo
    # starts on a clean slate and builds everything up live).
    from app.decide import commit, engine
    from app.decide.policy import Policy
    from app.governance import recorder
    pol = Policy(auto_approve_ceiling=750000, min_confidence=0.75, policy_version="t",
                 severity_overrides={})
    checks = [{"check": c, "status": "pass", "reason": ""} for c in
              ("po_lookup", "vendor_approved", "po_status", "total_tolerance",
               "line_reconciliation", "tax_present", "duplicate")]
    report = {"invoice_number": "INV-RST", "po_reference": "PO-5001", "matched_po": "PO-5001",
              "po_balance": 566400, "checks": checks}
    extr = {"invoice_number": "INV-RST", "vendor_name": "Dell", "po_reference": "PO-5001",
            "total": 566400, "extraction_confidence": {"overall": 0.95}}
    rid = recorder.start_run(invoice_number="INV-RST", vendor_name="Dell")
    commit.commit_decision(engine.decide(report, extr, pol), "PO-5001", 566400, rid)

    body = client.post("/admin/reset-demo", headers=_hdr("manager", MGR)).json()
    assert body == {"runs": 0, "tally": {}}
    summary = client.get("/dashboard/summary", headers=_hdr("manager", MGR)).json()
    assert summary["total_runs"] == 0

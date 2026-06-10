"""Live policy editing (skip-if-DB-down).

The headline test is acceptance criterion #5 end-to-end *via the API*: a
`PUT /policy` that lowers the ceiling flips the next decision's verdict — proving
policy is data the engine reads fresh, with no redeploy.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import service
from app.decide import engine
from app.decide.policy import load_policy
from app.main import app

MGR = "ffffffff-0000-0000-0000-000000000006"
CLERK = "99999999-0000-0000-0000-000000000007"


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


@pytest.fixture
def reset_db():
    from app.db.seed import seed
    seed()  # restores policy_config to the seeded defaults (ceiling 750000)
    yield
    seed()  # and restore again so an edit doesn't leak into other suites


def _hdr(role: str, user_id: str) -> dict[str, str]:
    token = service.create_access_token(
        {"user_id": user_id, "email": f"{user_id}@x.com", "role": role, "name": role})
    return {"Authorization": f"Bearer {token}"}


def _report():
    checks = [{"check": c, "status": "pass", "reason": ""} for c in
              ("po_lookup", "vendor_approved", "po_status",
               "total_tolerance", "line_reconciliation", "duplicate")]
    return {"invoice_number": "INV-1", "po_reference": "PO-5001", "matched_po": "PO-5001",
            "po_balance": 566400, "checks": checks}


def _extr():
    return {"invoice_number": "INV-1", "vendor_name": "Dell", "po_reference": "PO-5001",
            "total": 566400, "extraction_confidence": {"overall": 0.95}}


@requires_db
def test_get_policy_returns_row(client, reset_db):
    body = client.get("/policy", headers=_hdr("manager", MGR)).json()
    assert body["auto_approve_ceiling"] == 750000.0
    assert "policy_version" in body and "severity_overrides" in body


@requires_db
def test_put_policy_flips_next_verdict_via_api(client, reset_db):
    # Before: ceiling 750k → a 566400 invoice APPROVEs.
    assert engine.decide(_report(), _extr(), load_policy())["verdict"] == "APPROVE"

    r = client.put("/policy", headers=_hdr("manager", MGR),
                   json={"auto_approve_ceiling": 200000})
    assert r.status_code == 200
    assert r.json()["auto_approve_ceiling"] == 200000.0

    # After: the engine reads the new policy fresh → same evidence now FLAGs.
    d = engine.decide(_report(), _extr(), load_policy())
    assert d["verdict"] == "FLAG"
    assert d["review_payload"]["queue"] == "over_authority"


@requires_db
def test_put_policy_bumps_version_and_audits(client, reset_db):
    from app.db.connection import cursor
    before = client.get("/policy", headers=_hdr("manager", MGR)).json()["policy_version"]
    r = client.put("/policy", headers=_hdr("manager", MGR),
                   json={"min_confidence": 0.8})
    after = r.json()["policy_version"]
    assert after != before

    with cursor() as cur:
        cur.execute("SELECT actor_role, action_type, detail FROM governance_events "
                    "WHERE action_type='policy_change' ORDER BY event_id DESC LIMIT 1")
        role, atype, detail = cur.fetchone()
    assert role == "manager" and atype == "policy_change"
    assert detail["changed"] == {"min_confidence": 0.8}


@requires_db
def test_put_policy_severity_override(client, reset_db):
    r = client.put("/policy", headers=_hdr("manager", MGR),
                   json={"severity_overrides": {"total_tolerance": "REJECT"}})
    assert r.status_code == 200
    assert r.json()["severity_overrides"]["total_tolerance"] == "REJECT"


@requires_db
def test_put_policy_invalid_severity_400(client, reset_db):
    r = client.put("/policy", headers=_hdr("manager", MGR),
                   json={"severity_overrides": {"total_tolerance": "NOPE"}})
    assert r.status_code == 400


@requires_db
def test_put_policy_unknown_signal_400(client, reset_db):
    r = client.put("/policy", headers=_hdr("manager", MGR),
                   json={"severity_overrides": {"made_up_signal": "FLAG"}})
    assert r.status_code == 400

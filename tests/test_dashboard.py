"""Dashboard aggregates (skip-if-DB-down). Manager-only; counts match verdicts."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import service
from app.decide import commit, engine
from app.decide.policy import Policy
from app.governance import recorder
from app.main import app

MGR = "dddddddd-0000-0000-0000-000000000004"
CLERK = "eeeeeeee-0000-0000-0000-000000000005"

_PASS = Policy(auto_approve_ceiling=750000, min_confidence=0.75,
               policy_version="t", severity_overrides={})
_STRICT = Policy(auto_approve_ceiling=200000, min_confidence=0.75,
                 policy_version="t", severity_overrides={})


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
    from app.db.connection import cursor
    from app.db.seed import seed
    seed()
    with cursor() as cur:
        cur.execute("TRUNCATE review_actions, governance_events, validation_reports, "
                    "verdicts, invoices, pipeline_runs RESTART IDENTITY CASCADE")
    yield


def _hdr(role: str, user_id: str) -> dict[str, str]:
    token = service.create_access_token(
        {"user_id": user_id, "email": f"{user_id}@x.com", "role": role, "name": role})
    return {"Authorization": f"Bearer {token}"}


def _commit(inv, policy):
    checks = [{"check": c, "status": "pass", "reason": ""} for c in
              ("po_lookup", "vendor_approved", "po_status",
               "total_tolerance", "line_reconciliation", "duplicate")]
    report = {"invoice_number": inv, "po_reference": "PO-5001", "matched_po": "PO-5001",
              "po_balance": 566400, "checks": checks}
    extr = {"invoice_number": inv, "vendor_name": "Dell", "po_reference": "PO-5001",
            "total": 566400, "extraction_confidence": {"overall": 0.95}}
    run_id = recorder.start_run(invoice_number=inv, vendor_name="Dell",
                                actor_user_id=CLERK, actor_role="clerk")
    commit.commit_decision(engine.decide(report, extr, policy), "PO-5001", 566400, run_id)


@requires_db
def test_summary_counts_match_verdicts(client, reset_db):
    _commit("INV-1", _PASS)      # APPROVE
    _commit("INV-2", _STRICT)    # FLAG
    _commit("INV-3", _STRICT)    # FLAG
    body = client.get("/dashboard/summary", headers=_hdr("manager", MGR)).json()
    assert body["verdicts"]["APPROVE"] == 1
    assert body["verdicts"]["FLAG"] == 2
    assert body["needs_review"] == 2
    assert body["total_runs"] == 3
    assert "as_of" in body


@requires_db
def test_trends_returns_buckets(client, reset_db):
    _commit("INV-1", _PASS)
    _commit("INV-2", _STRICT)
    body = client.get("/dashboard/trends?days=7", headers=_hdr("manager", MGR)).json()
    assert body["days"] == 7
    assert len(body["trends"]) >= 1
    today = body["trends"][-1]
    assert today["APPROVE"] + today["FLAG"] + today["REJECT"] == today["total"]


@requires_db
def test_summary_forbidden_for_clerk(client, reset_db):
    assert client.get("/dashboard/summary", headers=_hdr("clerk", CLERK)).status_code == 403


@requires_db
def test_kpis_forbidden_for_clerk(client, reset_db):
    assert client.get("/dashboard/kpis", headers=_hdr("clerk", CLERK)).status_code == 403


@requires_db
def test_kpis_values_match_verdicts(client, reset_db):
    _commit("INV-1", _PASS)      # APPROVE (draws PO-5001 down)
    _commit("INV-2", _STRICT)    # FLAG over_authority
    _commit("INV-3", _STRICT)    # FLAG over_authority
    body = client.get("/dashboard/kpis", headers=_hdr("manager", MGR)).json()
    assert body["totals"] == {"verdicts": 3, "approve": 1, "flag": 2, "reject": 0}
    assert abs(body["kpis"]["stp_rate"]["value"] - 1 / 3) < 1e-9
    # Touchless savings = approve_count × (manual_cost − auto_cost) = 1 × (900 − 170).
    assert body["kpis"]["touchless_savings"]["value"] == 730.0
    assert body["flags_by_reason"] == {"over_authority": 2}
    assert body["costs"]["manual_cost_per_invoice"] == 900.0

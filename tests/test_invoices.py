"""Run list/detail scoping (skip-if-DB-down).

A clerk sees only their own runs; a manager sees all. Run detail 404s when a
clerk asks for someone else's run. The ?verdict filter narrows the list.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import service
from app.decide import commit, engine
from app.decide.policy import Policy
from app.governance import recorder
from app.main import app

CLERK_A = "aaaaaaaa-0000-0000-0000-000000000001"
CLERK_B = "bbbbbbbb-0000-0000-0000-000000000002"
MGR = "cccccccc-0000-0000-0000-000000000003"

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


def _report(inv, balance=566400):
    checks = [{"check": c, "status": "pass", "reason": ""} for c in
              ("po_lookup", "vendor_approved", "po_status",
               "total_tolerance", "line_reconciliation", "duplicate")]
    return {"invoice_number": inv, "po_reference": "PO-5001", "matched_po": "PO-5001",
            "po_balance": balance, "checks": checks}


def _extr(inv, total=566400):
    return {"invoice_number": inv, "vendor_name": "Dell", "po_reference": "PO-5001",
            "total": total, "extraction_confidence": {"overall": 0.95}}


def _make_run(owner: str, inv: str, policy: Policy) -> str:
    run_id = recorder.start_run(invoice_number=inv, vendor_name="Dell",
                                actor_user_id=owner, actor_role="clerk")
    verdict = engine.decide(_report(inv), _extr(inv), policy)
    commit.commit_decision(verdict, "PO-5001", 566400, run_id)
    return run_id


@requires_db
def test_clerk_sees_only_own_runs(client, reset_db):
    _make_run(CLERK_A, "INV-A1", _STRICT)
    _make_run(CLERK_B, "INV-B1", _STRICT)
    runs = client.get("/invoices/runs", headers=_hdr("clerk", CLERK_A)).json()["runs"]
    invoices = {r["invoice_number"] for r in runs}
    assert "INV-A1" in invoices and "INV-B1" not in invoices


@requires_db
def test_manager_sees_all_runs(client, reset_db):
    _make_run(CLERK_A, "INV-A1", _STRICT)
    _make_run(CLERK_B, "INV-B1", _STRICT)
    runs = client.get("/invoices/runs", headers=_hdr("manager", MGR)).json()["runs"]
    invoices = {r["invoice_number"] for r in runs}
    assert {"INV-A1", "INV-B1"}.issubset(invoices)


@requires_db
def test_verdict_filter(client, reset_db):
    _make_run(CLERK_A, "INV-APP", _PASS)      # APPROVE
    _make_run(CLERK_A, "INV-FLAG", _STRICT)   # FLAG (over ceiling)
    runs = client.get("/invoices/runs?verdict=FLAG",
                      headers=_hdr("clerk", CLERK_A)).json()["runs"]
    assert all(r["verdict"] == "FLAG" for r in runs)
    assert "INV-FLAG" in {r["invoice_number"] for r in runs}
    assert "INV-APP" not in {r["invoice_number"] for r in runs}


@requires_db
def test_run_detail_404_for_other_clerk(client, reset_db):
    run_id = _make_run(CLERK_A, "INV-A1", _STRICT)
    r = client.get(f"/invoices/runs/{run_id}", headers=_hdr("clerk", CLERK_B))
    assert r.status_code == 404


@requires_db
def test_run_detail_ok_for_owner_and_manager(client, reset_db):
    run_id = _make_run(CLERK_A, "INV-A1", _STRICT)
    assert client.get(f"/invoices/runs/{run_id}", headers=_hdr("clerk", CLERK_A)).status_code == 200
    r = client.get(f"/invoices/runs/{run_id}", headers=_hdr("manager", MGR))
    assert r.status_code == 200
    body = r.json()
    assert body["invoice_number"] == "INV-A1"
    assert body["verdict"]["verdict"] == "FLAG"
    assert any(e["stage"] == "decision" for e in body["events"])


@requires_db
def test_bad_verdict_filter_422(client, reset_db):
    assert client.get("/invoices/runs?verdict=MAYBE",
                      headers=_hdr("clerk", CLERK_A)).status_code == 422


@requires_db
def test_runs_list_has_amount_and_confidence(client, reset_db):
    run_id = _make_run(CLERK_A, "INV-A1", _STRICT)
    recorder.finish_run(run_id, overall_conf=0.95)  # as the orchestrator does
    runs = client.get("/invoices/runs", headers=_hdr("manager", MGR)).json()["runs"]
    row = next(r for r in runs if r["invoice_number"] == "INV-A1")
    assert row["invoice_total"] == 566400.0       # from the verdict
    assert row["overall_conf"] == 0.95            # from the run


@requires_db
def test_runs_list_surfaces_manual_override(client, reset_db):
    run_id = _make_run(CLERK_A, "INV-OV", _PASS)  # auto-APPROVE
    # a human manually rejects it (override) via the review action endpoint
    client.post(f"/review/{run_id}/action", headers=_hdr("manager", MGR),
                json={"action": "reject", "note": "override"})
    runs = client.get("/invoices/runs", headers=_hdr("manager", MGR)).json()["runs"]
    row = next(r for r in runs if r["invoice_number"] == "INV-OV")
    assert row["verdict"] == "APPROVE"     # the AI verdict is unchanged
    assert row["last_action"] == "reject"  # the human override is surfaced

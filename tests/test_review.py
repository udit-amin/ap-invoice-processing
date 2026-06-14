"""Review queue + effectful review actions (skip-if-DB-down).

A flagged run appears in the queue; an `approve` draws the matched PO down via
the same race-safe path the auto-decision uses; an over-committing approve is
refused (409, left flagged); `reject`/`escalate` are record-only.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import service
from app.decide import commit, engine
from app.decide.policy import Policy
from app.governance import recorder
from app.main import app

CLERK = "11111111-1111-1111-1111-111111111111"
MGR = "22222222-2222-2222-2222-222222222222"


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
    seed()  # restores reference data incl. PO-5001 balance 566400/open
    with cursor() as cur:
        cur.execute("TRUNCATE review_actions, governance_events, validation_reports, "
                    "verdicts, invoices, pipeline_runs RESTART IDENTITY CASCADE")
    yield


def _hdr(role: str, user_id: str) -> dict[str, str]:
    token = service.create_access_token(
        {"user_id": user_id, "email": f"{user_id}@x.com", "role": role, "name": role})
    return {"Authorization": f"Bearer {token}"}


def _report():
    checks = [{"check": c, "status": "pass", "reason": ""} for c in
              ("po_lookup", "vendor_approved", "po_status",
               "total_tolerance", "line_reconciliation", "duplicate")]
    return {"invoice_number": "DEL/2026/0412", "po_reference": "PO-5001",
            "matched_po": "PO-5001", "po_balance": 566400, "checks": checks}


def _extr():
    return {"invoice_number": "DEL/2026/0412", "vendor_name": "Dell",
            "po_reference": "PO-5001", "total": 566400,
            "extraction_confidence": {"overall": 0.95}}


def _make_flagged_run(actor_user_id: str = CLERK) -> str:
    """Commit an over-authority FLAG for PO-5001 (clean checks, low ceiling)."""
    run_id = recorder.start_run(invoice_number="DEL/2026/0412", vendor_name="Dell",
                                actor_user_id=actor_user_id, actor_role="clerk")
    # ceiling 200k < total 566400 → over_authority FLAG, but PO has the balance.
    strict = Policy(auto_approve_ceiling=200000, min_confidence=0.75,
                    policy_version="t", severity_overrides={})
    verdict = engine.decide(_report(), _extr(), strict)
    assert verdict["verdict"] == "FLAG"
    commit.commit_decision(verdict, "PO-5001", 566400, run_id)
    return run_id


@requires_db
def test_flagged_run_appears_in_queue(client, reset_db):
    run_id = _make_flagged_run()
    r = client.get("/review/queue", headers=_hdr("manager", MGR))
    assert r.status_code == 200
    ids = [item["run_id"] for item in r.json()["queue"]]
    assert run_id in ids


@requires_db
def test_approve_draws_po_down_and_leaves_queue(client, reset_db):
    from app.db.connection import cursor
    run_id = _make_flagged_run()

    r = client.post(f"/review/{run_id}/action",
                    headers=_hdr("manager", MGR),
                    json={"action": "approve", "note": "verified by phone"})
    assert r.status_code == 200
    assert r.json()["po_balance_after"] == 0.0

    # PO drawn down + closed.
    with cursor() as cur:
        cur.execute("SELECT remaining_balance, status FROM purchase_orders WHERE po_id='PO-5001'")
        bal, status = cur.fetchone()
    assert float(bal) == 0.0 and status == "closed"

    # review_actions row stamped with the manager.
    with cursor() as cur:
        cur.execute("SELECT action, actor_user_id, actor_role, po_balance_after "
                    "FROM review_actions WHERE run_id=%s", (run_id,))
        action, auid, arole, bal_after = cur.fetchone()
    assert action == "approve" and str(auid) == MGR and arole == "manager"
    assert float(bal_after) == 0.0

    # governance event recorded with action_type review_approve.
    with cursor() as cur:
        cur.execute("SELECT action_type, actor_role FROM governance_events "
                    "WHERE run_id=%s AND stage='review'", (run_id,))
        atype, ev_role = cur.fetchone()
    assert atype == "review_approve" and ev_role == "manager"

    # left the queue.
    q = client.get("/review/queue", headers=_hdr("manager", MGR)).json()["queue"]
    assert run_id not in [i["run_id"] for i in q]


@requires_db
def test_approve_on_empty_po_is_refused_and_stays_flagged(client, reset_db):
    from app.db.connection import cursor
    run_id = _make_flagged_run()
    with cursor() as cur:
        cur.execute("UPDATE purchase_orders SET remaining_balance=0, status='open' WHERE po_id='PO-5001'")

    r = client.post(f"/review/{run_id}/action",
                    headers=_hdr("manager", MGR), json={"action": "approve"})
    assert r.status_code == 409

    # PO untouched, no terminal action, still in the queue.
    with cursor() as cur:
        cur.execute("SELECT remaining_balance FROM purchase_orders WHERE po_id='PO-5001'")
        assert float(cur.fetchone()[0]) == 0.0
        cur.execute("SELECT count(*) FROM review_actions WHERE run_id=%s", (run_id,))
        assert cur.fetchone()[0] == 0
    q = client.get("/review/queue", headers=_hdr("manager", MGR)).json()["queue"]
    assert run_id in [i["run_id"] for i in q]


@requires_db
def test_reject_is_record_only_and_leaves_queue(client, reset_db):
    from app.db.connection import cursor
    run_id = _make_flagged_run()

    r = client.post(f"/review/{run_id}/action",
                    headers=_hdr("manager", MGR), json={"action": "reject", "note": "bad PO"})
    assert r.status_code == 200

    with cursor() as cur:
        cur.execute("SELECT remaining_balance FROM purchase_orders WHERE po_id='PO-5001'")
        assert float(cur.fetchone()[0]) == 566400.0  # unchanged
        cur.execute("SELECT action_type FROM governance_events WHERE run_id=%s AND stage='review'", (run_id,))
        assert cur.fetchone()[0] == "review_reject"
    q = client.get("/review/queue", headers=_hdr("manager", MGR)).json()["queue"]
    assert run_id not in [i["run_id"] for i in q]


@requires_db
def test_escalate_keeps_item_in_queue(client, reset_db):
    run_id = _make_flagged_run()
    r = client.post(f"/review/{run_id}/action",
                    headers=_hdr("clerk", CLERK), json={"action": "escalate", "note": "need mgr"})
    assert r.status_code == 200
    q = client.get("/review/queue", headers=_hdr("manager", MGR)).json()["queue"]
    assert run_id in [i["run_id"] for i in q]  # escalate is not terminal


@requires_db
def test_action_on_unknown_run_404(client, reset_db):
    r = client.post("/review/00000000-0000-0000-0000-000000000099/action",
                    headers=_hdr("manager", MGR), json={"action": "approve"})
    assert r.status_code == 404


@requires_db
def test_invalid_action_422(client, reset_db):
    run_id = _make_flagged_run()
    r = client.post(f"/review/{run_id}/action",
                    headers=_hdr("manager", MGR), json={"action": "frobnicate"})
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# Review detail + file (powers the three flag-type views; either role may open
# any queue item — the queue is global).
# --------------------------------------------------------------------------- #

@requires_db
def test_review_detail_returns_context(client, reset_db):
    run_id = _make_flagged_run()
    r = client.get(f"/review/{run_id}", headers=_hdr("clerk", CLERK))
    assert r.status_code == 200
    d = r.json()
    assert d["verdict"] == "FLAG"
    assert d["review_payload"]["queue"] == "over_authority"
    assert d["invoice_total"] == 566400.0
    assert d["auto_approve_ceiling_applied"] == 200000.0
    assert any(dr["signal"] == "over_authority" for dr in d["drivers"])


@requires_db
def test_review_detail_404_for_unknown(client, reset_db):
    r = client.get("/review/00000000-0000-0000-0000-000000000099", headers=_hdr("manager", MGR))
    assert r.status_code == 404


@requires_db
def test_review_detail_includes_line_side_by_side(client, reset_db):
    """A line-variance FLAG surfaces the per-line invoice-vs-PO comparison."""
    from app.validate.validator import validate
    extr = {
        "invoice_number": "LINEVAR-1",
        "vendor_name": "Dell Technologies India Pvt Ltd",
        "po_reference": "PO-5001", "total": 566400,
        "line_items": [
            {"description": "Latitude 5440 Laptop", "quantity": 6, "unit_price": 80000, "is_bundle": False},
            {"description": 'UltraSharp 24" Monitor', "quantity": 5, "unit_price": 24000, "is_bundle": False},
        ],
        "tax": {"treatment": "separated", "rate_pct": 18},
        "extraction_confidence": {"overall": 0.95},
    }
    run_id = recorder.start_run(invoice_number="LINEVAR-1", vendor_name="Dell",
                                actor_user_id=CLERK, actor_role="clerk")
    report = validate(extr, run_id=run_id)
    verdict = engine.decide(report, extr, Policy(auto_approve_ceiling=750000,
                            min_confidence=0.75, policy_version="t", severity_overrides={}))
    assert verdict["verdict"] == "FLAG"
    commit.commit_decision(verdict, report.get("matched_po"), 566400, run_id)

    d = client.get(f"/review/{run_id}", headers=_hdr("manager", MGR)).json()
    assert d["review_payload"]["queue"] == "line_variance"
    assert isinstance(d["line_reconciliation"], list) and d["line_reconciliation"]
    assert d["checks"] is not None


@requires_db
def test_review_file_404_then_streams(client, reset_db):
    run_id = _make_flagged_run()
    assert client.get(f"/review/{run_id}/file", headers=_hdr("clerk", CLERK)).status_code == 404
    recorder.store_invoice_file(run_id, b"%PDF-1.4 scan", filename="x.pdf")
    r = client.get(f"/review/{run_id}/file", headers=_hdr("manager", MGR))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content == b"%PDF-1.4 scan"


@requires_db
def test_review_preview_renders_png(client, reset_db):
    import fitz
    run_id = _make_flagged_run()
    # no file stored yet → 404
    assert client.get(f"/review/{run_id}/preview", headers=_hdr("manager", MGR)).status_code == 404
    # a real one-page PDF renders to a PNG (either role)
    doc = fitz.open()
    doc.new_page()
    pdf = doc.tobytes()
    doc.close()
    recorder.store_invoice_file(run_id, pdf, filename="x.pdf")
    r = client.get(f"/review/{run_id}/preview", headers=_hdr("clerk", CLERK))
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

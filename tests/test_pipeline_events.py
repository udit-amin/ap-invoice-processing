"""POST /invoices/process returns the event trail and persists the source file +
extraction (skip-if-DB-down).

`extract` is monkeypatched to a canned payload so no model is called — the focus
here is the orchestrator's new UI-support side effects, not extraction itself.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import service
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
    seed()
    with cursor() as cur:
        cur.execute("TRUNCATE review_actions, governance_events, validation_reports, "
                    "verdicts, invoices, pipeline_runs RESTART IDENTITY CASCADE")
    yield


def _hdr(role: str, user_id: str) -> dict[str, str]:
    token = service.create_access_token(
        {"user_id": user_id, "email": f"{user_id}@x.com", "role": role, "name": role})
    return {"Authorization": f"Bearer {token}"}


_CANNED = {
    "invoice_number": "PROC-1", "vendor_name": "Dell Technologies India Pvt Ltd",
    "po_reference": "PO-5001", "total": 566400, "source_type": "text",
    "line_items": [], "tax": {"treatment": "separated", "rate_pct": 18},
    "extraction_confidence": {"overall": 0.95, "total": 0.95}, "error": None,
}


@requires_db
def test_process_returns_events_and_persists_file(client, reset_db, monkeypatch):
    monkeypatch.setattr("app.pipeline.orchestrator.extract", lambda path: dict(_CANNED))
    files = {"file": ("proc.pdf", b"%PDF-1.4 fake invoice bytes", "application/pdf")}
    r = client.post("/invoices/process", headers=_hdr("clerk", CLERK), files=files)
    assert r.status_code == 200
    body = r.json()

    # The trail comes back on the response so the UI can replay it live (§4.2).
    stages = {e["stage"] for e in body["events"]}
    assert {"ingest", "extract", "match", "validate", "decision"} <= stages

    run_id = body["run_id"]
    # Source PDF persisted verbatim.
    found = recorder.fetch_invoice_file(run_id)
    assert found is not None and found[2] == b"%PDF-1.4 fake invoice bytes"
    # Full extraction (incl. per-field confidence) persisted on the run.
    from app.db.connection import cursor
    with cursor() as cur:
        cur.execute("SELECT extraction FROM pipeline_runs WHERE run_id=%s", (run_id,))
        assert cur.fetchone()[0]["invoice_number"] == "PROC-1"


@requires_db
def test_process_is_clerk_only(client, reset_db, monkeypatch):
    monkeypatch.setattr("app.pipeline.orchestrator.extract", lambda path: dict(_CANNED))
    files = {"file": ("proc.pdf", b"%PDF-1.4 fake", "application/pdf")}
    r = client.post("/invoices/process", headers=_hdr("manager", MGR), files=files)
    assert r.status_code == 403

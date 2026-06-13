"""Database-backed integration tests for the Postgres + governance layer.

Skip cleanly when Postgres is unreachable (mirrors the requires_api pattern in
test_extraction.py).  Each test starts from a clean operational state; reference
data is seeded once per session.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _db_available() -> bool:
    try:
        from app.db.connection import cursor
        with cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _db_available(), reason="Postgres not reachable — DB tests skipped.",
)

pytestmark = requires_db


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session", autouse=True)
def _seed_once():
    from app.db.seed import seed
    seed()
    yield


@pytest.fixture(autouse=True)
def _clean_operational():
    """Truncate operational tables before each test; leave reference data."""
    from app.db.connection import cursor
    with cursor() as cur:
        cur.execute(
            "TRUNCATE governance_events, validation_reports, invoices, "
            "pipeline_runs RESTART IDENTITY CASCADE"
        )
    yield


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _line(desc, qty, unit_price, is_bundle=False):
    return {
        "description": desc, "quantity": qty, "unit_price": unit_price,
        "line_total": (qty or 0) * (unit_price or 0),
        "is_bundle": is_bundle, "bundle_components": [],
    }


def _extracted(inv_no, vendor, po_ref, total, lines, treatment="separated", rate=18):
    return {
        "source_type": "text", "invoice_number": inv_no, "vendor_name": vendor,
        "invoice_date": "2026-06-01", "po_reference": po_ref, "currency": "INR",
        "line_items": lines, "subtotal": None,
        "tax": {"amount": None, "rate_pct": rate, "treatment": treatment},
        "total": total,
        "extraction_confidence": {"invoice_number": 0.95, "vendor_name": 0.95,
                                  "po_reference": 0.95, "total": 0.95, "overall": 0.95},
        "extraction_notes": [], "error": None,
    }


def _dell_normal():
    return _extracted("DEL/2026/0412", "Dell Technologies India Pvt Ltd", "PO-5001",
                      566400, [_line("Latitude 5440 Laptop", 5, 72000),
                               _line('UltraSharp 24" Monitor', 5, 24000)])


def _run_with_governance(extracted):
    """Mirror the pipeline's ingest/extract events + validate, returning run_id+report."""
    from app.governance import recorder
    from app.validate.validator import validate
    run_id = recorder.start_run(
        invoice_path="test.pdf",
        invoice_number=extracted.get("invoice_number"),
        vendor_name=extracted.get("vendor_name"),
    )
    recorder.log_event(run_id, recorder.INGEST, recorder.OK, {"invoice_path": "test.pdf"})
    recorder.log_event(run_id, recorder.EXTRACT, recorder.OK, {"source_type": "text"})
    report = validate(extracted, run_id=run_id)
    recorder.finish_run(run_id, overall_conf=0.95)
    return run_id, report


# --------------------------------------------------------------------------- #
# Loaders read reference data from Postgres
# --------------------------------------------------------------------------- #
def test_load_po_database_shape():
    from app.validate.loader import load_po_database
    po_db = load_po_database()
    assert "PO-5001" in po_db
    po = po_db["PO-5001"]
    assert po["status"] == "open"
    assert po["tolerance_pct"] == 3.0
    assert len(po["expected_line_items"]) == 2
    assert po["expected_line_items"][0]["unit_price"] == 72000.0


def test_load_vendor_registry_shape():
    from app.validate.loader import load_vendor_registry
    reg = load_vendor_registry()
    by_id = {v["vendor_id"]: v for v in reg}
    assert by_id["V-001"]["approved"] is True
    assert by_id["V-010"]["approved"] is False


# --------------------------------------------------------------------------- #
# validate() against the live database
# --------------------------------------------------------------------------- #
def test_validate_full_report_passes_for_dell():
    from app.validate.validator import validate
    report = validate(_dell_normal())
    by_name = {c["check"]: c for c in report["checks"]}
    assert set(by_name) == {"po_lookup", "vendor_approved", "po_status",
                            "total_tolerance", "line_reconciliation", "duplicate"}
    assert all(c["status"] == "pass" for c in report["checks"])
    assert report["matched_po"] == "PO-5001"


def test_po_not_found_short_circuits():
    from app.validate.validator import validate
    extracted = _extracted("X/1", "Dell Technologies India Pvt Ltd", "PO-9999",
                           10000, [_line("Widget", 1, 10000)])
    report = validate(extracted)
    by_name = {c["check"]: c for c in report["checks"]}
    assert by_name["po_lookup"]["status"] == "fail"
    assert by_name["po_status"]["status"] == "skip"
    assert by_name["total_tolerance"]["status"] == "skip"
    assert by_name["line_reconciliation"]["status"] == "skip"
    assert by_name["vendor_approved"]["status"] == "pass"


def test_no_verdict_field_anywhere():
    from app.validate.validator import validate
    report = validate(_dell_normal())

    def _keys(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k
                yield from _keys(v)
        elif isinstance(obj, list):
            for it in obj:
                yield from _keys(it)

    forbidden = {"approve", "reject", "decision", "verdict"}
    assert not (forbidden & {k.lower() for k in _keys(report)})


# --------------------------------------------------------------------------- #
# Race-proof duplicate detection
# --------------------------------------------------------------------------- #
def test_duplicate_flips_pass_to_fail():
    from app.validate.validator import validate
    first = validate(_dell_normal())
    second = validate(_dell_normal())
    dup1 = next(c for c in first["checks"] if c["check"] == "duplicate")
    dup2 = next(c for c in second["checks"] if c["check"] == "duplicate")
    assert dup1["status"] == "pass"
    assert dup2["status"] == "fail"
    assert "duplicate" in dup2["reason"].lower()


def test_duplicate_constraint_is_atomic():
    """Direct insert proves the UNIQUE constraint arbitrates, not app-level check."""
    import psycopg
    from app.db.connection import cursor
    with cursor() as cur:
        cur.execute(
            "INSERT INTO invoices (invoice_number, vendor_name) VALUES (%s, %s)",
            ("DUP/1", "ACME"),
        )
    with pytest.raises(psycopg.errors.UniqueViolation):
        with cursor() as cur:
            cur.execute(
                "INSERT INTO invoices (invoice_number, vendor_name) VALUES (%s, %s)",
                ("DUP/1", "ACME"),
            )


# --------------------------------------------------------------------------- #
# Governance events persisted at every stage
# --------------------------------------------------------------------------- #
def test_governance_events_recorded_for_all_stages():
    from app.db.connection import cursor
    run_id, _ = _run_with_governance(_dell_normal())
    with cursor() as cur:
        cur.execute(
            "SELECT DISTINCT stage FROM governance_events WHERE run_id = %s", (run_id,)
        )
        stages = {row[0] for row in cur.fetchall()}
    assert {"ingest", "extract", "match", "validate"}.issubset(stages)


def test_validation_report_persisted():
    from app.db.connection import cursor
    run_id, report = _run_with_governance(_dell_normal())
    with cursor() as cur:
        cur.execute(
            "SELECT passed, failed, skipped FROM validation_reports WHERE run_id = %s",
            (run_id,),
        )
        row = cur.fetchone()
    assert row is not None
    passed, failed, skipped = row
    assert passed == report["summary"]["passed"]


# --------------------------------------------------------------------------- #
# Audit REST endpoint
# --------------------------------------------------------------------------- #
def test_audit_endpoint_returns_trail(client, auth_header):
    _run_with_governance(_dell_normal())
    r = client.get("/audit/DEL/2026/0412", headers=auth_header("manager"))
    assert r.status_code == 200
    body = r.json()
    assert body["invoice_number"] == "DEL/2026/0412"
    assert len(body["runs"]) == 1
    stages = {e["stage"] for e in body["runs"][0]["events"]}
    assert {"ingest", "extract", "match", "validate"}.issubset(stages)
    assert body["latest_report"] is not None


def test_audit_endpoint_404_for_unknown_invoice(client, auth_header):
    r = client.get("/audit/NOPE-9999", headers=auth_header("manager"))
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# Actor identity on the trail (PR2): who ran the pipeline
# --------------------------------------------------------------------------- #
class _Actor:
    user_id = "12121212-0000-0000-0000-000000000001"
    role = "clerk"
    name = "Priya Nair"
    email = "priya@acmecorp.com"


def test_audit_trail_records_actor(client, auth_header):
    from app.decide import commit, engine
    from app.decide.policy import load_policy
    from app.governance import recorder

    run_id = recorder.start_run(invoice_number="DEL/2026/0412", vendor_name="Dell",
                                actor_user_id=_Actor.user_id, actor_role=_Actor.role)
    recorder.log_event(run_id, recorder.INGEST, recorder.OK, {"x": 1},
                       actor=_Actor.name, actor_user_id=_Actor.user_id,
                       actor_role=_Actor.role, action_type="pipeline_run")
    verdict = engine.decide(
        {"invoice_number": "DEL/2026/0412", "po_reference": "PO-5001",
         "matched_po": "PO-5001", "po_balance": 566400,
         "checks": [{"check": c, "status": "pass", "reason": ""} for c in
                    ("po_lookup", "vendor_approved", "po_status",
                     "total_tolerance", "line_reconciliation", "duplicate")]},
        {"invoice_number": "DEL/2026/0412", "vendor_name": "Dell",
         "po_reference": "PO-5001", "total": 566400,
         "extraction_confidence": {"overall": 0.95}},
        load_policy())
    commit.commit_decision(verdict, "PO-5001", 566400, run_id, actor=_Actor)

    body = client.get("/audit/DEL/2026/0412", headers=auth_header("manager")).json()
    events = body["runs"][0]["events"]
    ingest = next(e for e in events if e["stage"] == "ingest")
    decision = next(e for e in events if e["stage"] == "decision")
    assert ingest["actor_user_id"] == _Actor.user_id
    assert ingest["action_type"] == "pipeline_run"
    assert decision["actor_role"] == "clerk"
    assert decision["action_type"] == "pipeline_run"

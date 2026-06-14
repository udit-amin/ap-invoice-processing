"""Decision-engine tests.

Pure resolver tests inject evidence + a Policy and need no infrastructure.
DB-integration tests (skip-if-down) cover the APPROVE balance decrement, the
race-safe commit downgrade, persistence, and the /audit surface.
"""
from __future__ import annotations

import pytest

from app.decide import engine
from app.decide.policy import Policy, APPROVE, FLAG, REJECT


# --------------------------------------------------------------------------- #
# Helpers (pure)
# --------------------------------------------------------------------------- #
POLICY = Policy(auto_approve_ceiling=750000, min_confidence=0.75,
                policy_version="2026.06.1", severity_overrides={})


def _chk(name, status, reason=""):
    return {"check": name, "status": status, "reason": reason}


def _report(checks, inv="INV-1", po="PO-5001", balance=566400):
    return {"invoice_number": inv, "po_reference": po, "matched_po": po,
            "po_balance": balance, "checks": checks}


def _extr(total=566400, conf=0.95, inv="INV-1", vendor="Dell", po="PO-5001"):
    return {"invoice_number": inv, "vendor_name": vendor, "po_reference": po,
            "total": total, "extraction_confidence": {"overall": conf}}


_PASS6 = [_chk(c, "pass") for c in
          ("po_lookup", "vendor_approved", "po_status",
           "total_tolerance", "line_reconciliation", "duplicate")]


def _with(check_overrides, **kw):
    """A 6-check report with some checks overridden."""
    checks = []
    for c in _PASS6:
        name = c["check"]
        if name in check_overrides:
            status, reason = check_overrides[name]
            checks.append(_chk(name, status, reason))
        else:
            checks.append(_chk(name, "pass"))
    return _report(checks, **kw)


# --------------------------------------------------------------------------- #
# The verdict matrix (pure)
# --------------------------------------------------------------------------- #
def test_clean_invoice_approves():
    d = engine.decide(_report(_PASS6), _extr(), POLICY)
    assert d["verdict"] == APPROVE
    assert d["requires_human_review"] is False
    assert d["review_payload"] is None


def test_line_mismatch_flags_not_rejects():
    # edge_4: total within tolerance (pass) but lines fail → FLAG, not REJECT
    d = engine.decide(
        _with({"line_reconciliation": ("fail", "2 of 2 line(s) mismatch")}),
        _extr(), POLICY)
    assert d["verdict"] == FLAG
    assert d["review_payload"]["queue"] == "line_variance"


def test_low_confidence_flags():
    d = engine.decide(_report(_PASS6), _extr(conf=0.62), POLICY)
    assert d["verdict"] == FLAG
    assert d["review_payload"]["queue"] == "low_confidence"


def test_over_ceiling_flags():
    d = engine.decide(_report(_PASS6, balance=802400), _extr(total=802400), POLICY)
    assert d["verdict"] == FLAG
    assert d["review_payload"]["queue"] == "over_authority"


def test_missing_tax_flags():
    # All standard checks pass; only tax_present fails → FLAG, queued as missing_tax.
    checks = _PASS6 + [_chk("tax_present", "fail", "Invoice shows no tax")]
    d = engine.decide(_report(checks), _extr(), POLICY)
    assert d["verdict"] == FLAG
    assert d["review_payload"]["queue"] == "missing_tax"


def test_tax_present_pass_or_skip_does_not_escalate():
    # pass = tax shown; skip = treatment unknown (the answer-key/dry-run shape).
    # Neither escalates, so the verdict matrix is unaffected when tax isn't modelled.
    for status in ("pass", "skip"):
        checks = _PASS6 + [_chk("tax_present", status)]
        assert engine.decide(_report(checks), _extr(), POLICY)["verdict"] == APPROVE


def test_po_not_found_plus_vendor_rejects():
    d = engine.decide(
        _with({"po_lookup": ("fail", "PO-9999 not found"),
               "vendor_approved": ("fail", "not in registry"),
               "po_status": ("skip", ""), "total_tolerance": ("skip", ""),
               "line_reconciliation": ("skip", "")}, po="PO-9999"),
        _extr(po="PO-9999", total=236000), POLICY)
    assert d["verdict"] == REJECT


def test_closed_po_rejects_despite_clean_lines():
    # severity precedence: po_status REJECT dominates a tolerance FLAG
    d = engine.decide(
        _with({"po_status": ("fail", "PO-5003 is closed"),
               "total_tolerance": ("fail", "zero balance"),
               "line_reconciliation": ("skip", "")}, po="PO-5003", balance=0),
        _extr(po="PO-5003", total=141600), POLICY)
    assert d["verdict"] == REJECT
    assert "not open for billing" in d["reason"]


def test_duplicate_rejects():
    d = engine.decide(
        _with({"duplicate": ("fail", "Duplicate of run X")}), _extr(), POLICY)
    assert d["verdict"] == REJECT


# --------------------------------------------------------------------------- #
# Reasoning + policy properties (pure)
# --------------------------------------------------------------------------- #
def test_three_distinct_flag_reasons():
    line = engine.decide(_with({"line_reconciliation": ("fail", "2 of 2 mismatch")}),
                         _extr(), POLICY)["reason"]
    conf = engine.decide(_report(_PASS6), _extr(conf=0.62), POLICY)["reason"]
    auth = engine.decide(_report(_PASS6, balance=802400),
                         _extr(total=802400), POLICY)["reason"]
    assert len({line, conf, auth}) == 3


def test_reason_reproducible_byte_for_byte():
    r1 = engine.decide(_with({"line_reconciliation": ("fail", "2 of 2 mismatch")}),
                       _extr(), POLICY)["reason"]
    r2 = engine.decide(_with({"line_reconciliation": ("fail", "2 of 2 mismatch")}),
                       _extr(), POLICY)["reason"]
    assert r1 == r2


def test_no_llm_marker_and_policy_version_stamped():
    d = engine.decide(_report(_PASS6), _extr(), POLICY)
    assert d["policy_version"] == "2026.06.1"
    assert d["auto_approve_ceiling_applied"] == 750000


def test_ceiling_change_flips_verdict_no_code_change():
    # AC#5: same evidence, lower ceiling via policy data → APPROVE becomes FLAG
    strict = Policy(auto_approve_ceiling=200000, min_confidence=0.75,
                    policy_version="test", severity_overrides={})
    d = engine.decide(_report(_PASS6), _extr(total=566400), strict)
    assert d["verdict"] == FLAG
    assert d["review_payload"]["queue"] == "over_authority"


def test_severity_override_promotes_tolerance_to_reject():
    # data-driven map: make a tolerance failure a REJECT
    p = Policy(auto_approve_ceiling=750000, min_confidence=0.75, policy_version="t",
               severity_overrides={"total_tolerance": "REJECT"})
    d = engine.decide(_with({"total_tolerance": ("fail", "12% over")}), _extr(), p)
    assert d["verdict"] == REJECT


# --------------------------------------------------------------------------- #
# DB integration (skip-if-down)
# --------------------------------------------------------------------------- #
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
def _clean_db():
    from app.db.connection import cursor
    from app.db.seed import seed
    seed()  # restores reference data incl. PO balances
    with cursor() as cur:
        cur.execute("TRUNCATE governance_events, validation_reports, verdicts, "
                    "invoices, pipeline_runs RESTART IDENTITY CASCADE")
    yield


@requires_db
def test_approve_decrements_and_closes_po(_clean_db):
    from app.db.connection import cursor
    from app.decide import commit
    from app.decide.policy import load_policy
    rid = _run_id()
    verdict = engine.decide(_report(_PASS6), _extr(total=566400), load_policy())
    out = commit.commit_decision(verdict, "PO-5001", 566400, rid)
    assert out["verdict"] == APPROVE
    assert out["po_balance_after"] == 0.0
    with cursor() as cur:
        cur.execute("SELECT remaining_balance, status FROM purchase_orders WHERE po_id='PO-5001'")
        bal, status = cur.fetchone()
    assert float(bal) == 0.0 and status == "closed"


@requires_db
def test_commit_downgrades_on_insufficient_balance(_clean_db):
    from app.db.connection import cursor
    from app.decide import commit
    from app.decide.policy import load_policy
    # Simulate a concurrent draw-down: PO-5001 already emptied.
    with cursor() as cur:
        cur.execute("UPDATE purchase_orders SET remaining_balance=0, status='open' WHERE po_id='PO-5001'")
    verdict = engine.decide(_report(_PASS6), _extr(total=566400), load_policy())
    assert verdict["verdict"] == APPROVE  # resolver approved against stale balance
    out = commit.commit_decision(verdict, "PO-5001", 566400, _run_id())
    assert out["verdict"] == FLAG
    assert out["po_balance_after"] is None
    assert "insufficient at commit time" in out["reason"]
    with cursor() as cur:
        cur.execute("SELECT remaining_balance FROM purchase_orders WHERE po_id='PO-5001'")
        assert float(cur.fetchone()[0]) == 0.0  # not over-committed


@requires_db
def test_verdict_persisted_and_on_audit(_clean_db):
    from app.decide import commit
    from app.decide.policy import load_policy
    from app.governance import recorder
    rid = recorder.start_run(invoice_number="DEL/2026/0412", vendor_name="Dell")
    verdict = engine.decide(_report(_PASS6, inv="DEL/2026/0412"),
                            _extr(inv="DEL/2026/0412"), load_policy())
    commit.commit_decision(verdict, "PO-5001", 566400, rid)
    trail = recorder.fetch_audit_trail("DEL/2026/0412")
    assert trail["latest_verdict"] is not None
    assert trail["latest_verdict"]["verdict"] == APPROVE
    assert trail["latest_verdict"]["policy_version"] == "2026.06.1"


def _run_id():
    from app.governance import recorder
    return recorder.start_run(invoice_number="INV-1", vendor_name="Dell")

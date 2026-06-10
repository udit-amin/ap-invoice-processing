"""Orchestrate the six validation checks and produce a structured evidence report.

This module gathers evidence only.  It never sets an approve / reject / decision
field — that belongs to the decision engine (next module).

When a run_id is supplied, each check also emits an append-only governance event
(stage = match for PO lookup + vendor; stage = validate for status, tolerance,
line reconciliation, duplicate) and the full report is persisted.
"""
from __future__ import annotations

import datetime

from src.validate import checks, loader
from src.governance import recorder

_SKIP_REASON_NO_PO = "PO not found — no PO to compare against"

# Which pipeline stage each check belongs to in the audit trail.
_STAGE = {
    "po_lookup":            recorder.MATCH,
    "vendor_approved":      recorder.MATCH,
    "po_status":            recorder.VALIDATE,
    "total_tolerance":      recorder.VALIDATE,
    "line_reconciliation":  recorder.VALIDATE,
    "duplicate":            recorder.VALIDATE,
}

# validation status -> governance event status
_EVENT_STATUS = {"pass": recorder.OK, "fail": recorder.FAIL, "skip": recorder.SKIP}


def validate(
    extracted: dict,
    po_db: dict[str, dict] | None = None,
    vendor_registry: list[dict] | None = None,
    run_id: str | None = None,
) -> dict:
    """Run all six checks and return a validation report.

    Reference data is loaded from Postgres when not injected — tests inject
    po_db / vendor_registry dicts to stay infra-free.  Duplicate detection and
    governance event writes always go to the database (best-effort).
    """
    ts_start = _ts()
    if po_db is None:
        po_db = loader.load_po_database()
    if vendor_registry is None:
        vendor_registry = loader.load_vendor_registry()

    inv_number = extracted.get("invoice_number")
    inv_vendor = extracted.get("vendor_name")
    inv_po_ref = extracted.get("po_reference")
    inv_total  = extracted.get("total")

    all_checks: list[dict] = []

    def _emit(check: dict) -> dict:
        all_checks.append(check)
        recorder.log_event(
            run_id,
            _STAGE.get(check["check"], recorder.VALIDATE),
            _EVENT_STATUS.get(check["status"], recorder.WARN),
            detail={k: v for k, v in check.items() if k != "detail"},
        )
        return check

    # --- Check 1: PO lookup (match stage) ---
    po_result, matched_po = checks.check_po_lookup(inv_po_ref, po_db)
    _emit(po_result)

    # --- Check 2: Vendor approval (match stage; always runs) ---
    _emit(checks.check_vendor_approval(inv_vendor, vendor_registry))

    # --- Checks 3-5: PO-dependent; skip when PO not found ---
    if matched_po is None:
        for name in ("po_status", "total_tolerance", "line_reconciliation"):
            _emit({"check": name, "status": "skip", "reason": _SKIP_REASON_NO_PO})
    else:
        _emit(checks.check_po_status(matched_po))
        _emit(checks.check_total_tolerance(inv_total, matched_po))
        _emit(checks.check_line_reconciliation(extracted, matched_po))

    # --- Check 6: Duplicate detection (validate stage; always runs) ---
    _emit(checks.check_duplicate(inv_number, inv_vendor, run_id))

    ts_end = _ts()
    summary = {
        "passed":  sum(1 for c in all_checks if c["status"] == "pass"),
        "failed":  sum(1 for c in all_checks if c["status"] == "fail"),
        "skipped": sum(1 for c in all_checks if c["status"] == "skip"),
    }
    report = {
        "invoice_number": inv_number,
        "po_reference":   inv_po_ref,
        "matched_po":     matched_po["po_id"] if matched_po else None,
        "po_balance":     matched_po["remaining_balance"] if matched_po else None,
        "checks":         all_checks,
        "summary":        summary,
        "events": [
            {"stage": "match",    "status": "ok", "ts": ts_start},
            {"stage": "validate", "status": "ok", "ts": ts_end},
        ],
    }

    recorder.record_validation_report(run_id, inv_number, report)
    return report


def _ts() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

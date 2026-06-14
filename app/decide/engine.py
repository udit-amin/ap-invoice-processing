"""The pure decision resolver: evidence + confidence + policy → verdict.

No database, no LLM. Given the same inputs it always produces the same verdict,
drivers, and reason — which is what makes the decision auditable and replayable.
The APPROVE side-effect (balance decrement) and persistence live in commit.py.
"""
from __future__ import annotations

from app.decide import reason as _reason
from app.decide.policy import APPROVE, FLAG, REJECT, SEVERITY_RANK, Policy

_REQUIRED_FIELDS = ["invoice_number", "vendor_name", "po_reference", "total"]
_CHECK_SIGNALS = {
    "po_lookup", "vendor_approved", "po_status",
    "total_tolerance", "line_reconciliation", "tax_present", "duplicate",
}


def _driver(signal, outcome, severity, detail=None) -> dict:
    return {"signal": signal, "outcome": outcome, "severity": severity, "detail": detail}


def _build_drivers(report: dict, extraction: dict, policy: Policy) -> list[dict]:
    drivers: list[dict] = []

    # The seven validation checks (evidence). Pass/skip contribute APPROVE (no
    # escalation); fail contributes the policy severity for that signal.
    for check in report.get("checks", []):
        name, status = check.get("check"), check.get("status")
        if name not in _CHECK_SIGNALS:
            continue
        severity = policy.severity_for(name) if status == "fail" else APPROVE
        drivers.append(_driver(name, status, severity, check.get("reason")))

    # Synthetic gates (only included when they fire).
    overall = (extraction.get("extraction_confidence") or {}).get("overall")
    if overall is not None and overall < policy.min_confidence:
        drivers.append(_driver(
            "low_confidence", "fail", policy.severity_for("low_confidence"),
            f"extraction confidence {overall:.2f} below minimum "
            f"{policy.min_confidence:.2f}",
        ))

    missing = [f for f in _REQUIRED_FIELDS if extraction.get(f) in (None, "")]
    if missing:
        drivers.append(_driver(
            "incomplete", "fail", policy.severity_for("incomplete"),
            f"missing required field(s): {', '.join(missing)}",
        ))

    total = extraction.get("total")
    if total is not None and total > policy.auto_approve_ceiling:
        drivers.append(_driver(
            "over_authority", "fail", policy.severity_for("over_authority"),
            f"invoice total ₹{total:,.0f} exceeds auto-approve ceiling "
            f"₹{policy.auto_approve_ceiling:,.0f}",
        ))

    return drivers


def _resolve(drivers: list[dict]) -> str:
    """Most severe contribution wins: REJECT > FLAG > APPROVE."""
    rank = max((SEVERITY_RANK.get(d["severity"], 0) for d in drivers), default=0)
    return {0: APPROVE, 1: FLAG, 2: REJECT}[rank]


def decide(report: dict, extraction: dict, policy: Policy) -> dict:
    """Render a verdict (pre-commit). po_balance_after / decided_at are filled
    by commit.py."""
    drivers = _build_drivers(report, extraction, policy)
    verdict = _resolve(drivers)

    overall = (extraction.get("extraction_confidence") or {}).get("overall")
    reason = _reason.assemble_reason(
        verdict, drivers, overall, policy.auto_approve_ceiling,
    )

    requires_review = verdict == FLAG
    review_payload = None
    if requires_review:
        review_payload = _reason.build_review_payload(
            drivers,
            extracted_total=extraction.get("total"),
            po_balance=(report.get("po_balance")),
        )

    return {
        "invoice_number": report.get("invoice_number"),
        "po_reference":   report.get("po_reference"),
        "verdict":        verdict,
        "reason":         reason,
        "drivers":        drivers,
        "requires_human_review": requires_review,
        "review_payload": review_payload,
        "confidence_overall": overall,
        "policy_version": policy.policy_version,
        "auto_approve_ceiling_applied": policy.auto_approve_ceiling,
        "po_balance_after": None,
        "decided_at":     None,
    }

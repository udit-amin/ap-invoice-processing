"""Deterministic, LLM-free reason + review-payload assembly.

The reason is built purely from the drivers, so it is reproducible byte-for-byte
on re-run (acceptance criterion #6). An LLM may rephrase it for display only —
never on the decision path.
"""
from __future__ import annotations

from app.decide.policy import APPROVE, FLAG, REJECT, SEVERITY_RANK

# Short clause per signal, used to assemble the sentence.
_PHRASE = {
    "po_lookup":           "the referenced PO could not be found",
    "vendor_approved":     "the vendor is not approved",
    "po_status":           "the PO is not open for billing",
    "duplicate":           "this invoice has already been processed",
    "total_tolerance":     "the invoice total is outside PO tolerance",
    "line_reconciliation": "line items do not reconcile with the PO",
    "low_confidence":      "extraction confidence is below the auto-approve minimum",
    "incomplete":          "required fields are missing",
    "over_authority":      "the amount exceeds the auto-approve ceiling",
}

# Priority for choosing the dominant FLAG driver (most specific first).
_FLAG_PRIORITY = [
    "line_reconciliation", "total_tolerance", "over_authority",
    "low_confidence", "incomplete",
]

_QUEUE = {
    "line_reconciliation": "line_variance",
    "total_tolerance":     "tolerance",
    "over_authority":      "over_authority",
    "low_confidence":      "low_confidence",
    "incomplete":          "incomplete",
}

_WHAT_TO_CHECK = {
    "line_variance":  "Confirm the billed quantities and unit prices against the PO before payment.",
    "tolerance":      "Confirm the overage against the PO is legitimate before payment.",
    "over_authority": "Amount exceeds auto-approve authority; route to an approver with sufficient limit.",
    "low_confidence": "Extraction was uncertain; verify the key fields against the source PDF.",
    "incomplete":     "Required fields are missing; complete them before the invoice can be auto-processed.",
}


def _escalating(drivers: list[dict]) -> list[dict]:
    """Non-APPROVE drivers, most severe first then by signal name (stable)."""
    esc = [d for d in drivers if SEVERITY_RANK.get(d["severity"], 0) > 0]
    return sorted(esc, key=lambda d: (-SEVERITY_RANK[d["severity"]], d["signal"]))


def _clause(driver: dict) -> str:
    base = _PHRASE.get(driver["signal"], driver["signal"])
    detail = driver.get("detail")
    return f"{base} ({detail})" if detail else base


def assemble_reason(
    verdict: str,
    drivers: list[dict],
    confidence_overall: float | None,
    ceiling: float,
) -> str:
    esc = _escalating(drivers)

    if verdict == REJECT:
        rejects = [d for d in esc if d["severity"] == REJECT]
        clauses = "; ".join(_clause(d) for d in rejects)
        return f"Rejected: {clauses}."

    if verdict == FLAG:
        flags = [d for d in esc if d["severity"] == FLAG]
        clauses = "; ".join(_clause(d) for d in flags)
        # Context: if lines were flagged but the total still matched, say so.
        ctx = ""
        signals = {d["signal"] for d in flags}
        if "line_reconciliation" in signals:
            tt = next((d for d in drivers if d["signal"] == "total_tolerance"
                       and d["outcome"] == "pass"), None)
            if tt:
                ctx = " The invoice total matches the PO within tolerance, so a human should confirm the substitution."
        return f"Flagged for review: {clauses}.{ctx}"

    # APPROVE
    conf = f"{confidence_overall:.2f}" if confidence_overall is not None else "n/a"
    return (
        f"Approved: all checks passed; extraction confidence {conf} meets the "
        f"minimum; invoice total is within the auto-approve ceiling "
        f"(₹{ceiling:,.0f})."
    )


def build_review_payload(
    drivers: list[dict],
    extracted_total: float | None,
    po_balance: float | None,
) -> dict:
    flags = [d["signal"] for d in drivers if d["severity"] == FLAG]
    signal = next((s for s in _FLAG_PRIORITY if s in flags), None)
    queue = _QUEUE.get(signal, "review")
    return {
        "queue": queue,
        "what_to_check": _WHAT_TO_CHECK.get(queue, "Manual review required."),
        "extracted_total": extracted_total,
        "po_balance": po_balance,
    }

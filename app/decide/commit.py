"""Persist a verdict and, on APPROVE only, decrement the PO balance — all in one
race-safe transaction.

The balance is re-checked *inside a row lock* (`SELECT … FOR UPDATE`): if a
concurrent partial invoice has already drawn the balance down so that this one no
longer fits within tolerance, the APPROVE is downgraded to FLAG rather than
over-committing the PO. The verdict row and the governance event are written on
the same connection, so the decision and its side-effect commit or roll back
together — never approve-without-decrement or decrement-without-record.
"""
from __future__ import annotations

import logging

from psycopg.types.json import Jsonb

from app.db.connection import cursor
from app.decide.policy import APPROVE, FLAG, REJECT
from app.decide import reason as _reason

log = logging.getLogger(__name__)

_CLOSE_EPSILON = 0.01

# verdict → governance event status (so the audit trail is filterable).
_EVENT_STATUS = {APPROVE: "ok", FLAG: "warn", REJECT: "fail"}


def _breaches_tolerance(invoice_total: float, balance: float, tol_pct: float) -> bool:
    if balance == 0:
        return invoice_total != 0
    return abs(invoice_total - balance) / balance * 100 > tol_pct


def commit_decision(
    verdict: dict,
    matched_po_id: str | None,
    invoice_total: float | None,
    run_id: str | None,
) -> dict:
    """Finalise and persist `verdict`. Returns the (possibly downgraded) verdict
    with `po_balance_after` and `decided_at` populated."""
    with cursor(autocommit=False) as cur:
        po_balance_after = None

        if verdict["verdict"] == APPROVE and matched_po_id and invoice_total is not None:
            cur.execute(
                """SELECT remaining_balance, tolerance_pct
                   FROM purchase_orders WHERE po_id = %s FOR UPDATE""",
                (matched_po_id,),
            )
            row = cur.fetchone()
            if row is not None:
                balance, tol_pct = float(row[0]), float(row[1])
                if _breaches_tolerance(invoice_total, balance, tol_pct):
                    # Concurrent draw-down: downgrade rather than over-commit.
                    _downgrade(verdict, balance)
                else:
                    new_balance = round(balance - invoice_total, 2)
                    new_status = "closed" if new_balance <= _CLOSE_EPSILON else "open"
                    cur.execute(
                        """UPDATE purchase_orders
                           SET remaining_balance = %s, status = %s
                           WHERE po_id = %s""",
                        (new_balance, new_status, matched_po_id),
                    )
                    po_balance_after = new_balance

        verdict["po_balance_after"] = po_balance_after

        cur.execute(
            """INSERT INTO verdicts
               (run_id, invoice_number, po_reference, verdict, reason, drivers,
                requires_human_review, review_payload, confidence_overall,
                policy_version, auto_approve_ceiling_applied, po_balance_after)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING decided_at""",
            (
                run_id, verdict["invoice_number"], verdict["po_reference"],
                verdict["verdict"], verdict["reason"], Jsonb(verdict["drivers"]),
                verdict["requires_human_review"],
                Jsonb(verdict["review_payload"]) if verdict["review_payload"] else None,
                verdict["confidence_overall"], verdict["policy_version"],
                verdict["auto_approve_ceiling_applied"], po_balance_after,
            ),
        )
        verdict["decided_at"] = cur.fetchone()[0].isoformat()

        cur.execute(
            """INSERT INTO governance_events (run_id, stage, status, detail)
               VALUES (%s, 'decision', %s, %s)""",
            (
                run_id, _EVENT_STATUS.get(verdict["verdict"], "ok"),
                Jsonb({
                    "verdict": verdict["verdict"],
                    "reason": verdict["reason"],
                    "policy_version": verdict["policy_version"],
                    "po_balance_after": po_balance_after,
                }),
            ),
        )

    return verdict


def _downgrade(verdict: dict, balance: float) -> None:
    """Turn a stale APPROVE into a FLAG at commit time (in place)."""
    verdict["verdict"] = FLAG
    verdict["requires_human_review"] = True
    verdict["reason"] = (
        "Flagged for review: PO balance insufficient at commit time "
        f"(remaining ₹{balance:,.0f}); another invoice drew the PO down since "
        "validation, so this one would over-commit it."
    )
    verdict["review_payload"] = {
        "queue": "tolerance",
        "what_to_check": _reason._WHAT_TO_CHECK["tolerance"],
        "extracted_total": None,
        "po_balance": balance,
    }
    verdict["drivers"] = list(verdict.get("drivers", [])) + [{
        "signal": "balance_at_commit", "outcome": "fail", "severity": FLAG,
        "detail": f"PO balance ₹{balance:,.0f} insufficient at commit time",
    }]

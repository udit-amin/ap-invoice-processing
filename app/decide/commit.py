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
from app.governance import recorder

log = logging.getLogger(__name__)

_CLOSE_EPSILON = 0.01

# verdict → governance event status (so the audit trail is filterable).
_EVENT_STATUS = {APPROVE: "ok", FLAG: "warn", REJECT: "fail"}


def _breaches_tolerance(invoice_total: float, balance: float, tol_pct: float) -> bool:
    if balance == 0:
        return invoice_total != 0
    return abs(invoice_total - balance) / balance * 100 > tol_pct


def draw_down_po(cur, po_id: str, invoice_total: float) -> tuple[float | None, bool, float | None]:
    """Race-safe PO draw-down inside an open transaction.

    Locks the PO row (`SELECT … FOR UPDATE`), and if the invoice still fits
    within tolerance, decrements `remaining_balance` (closing the PO at zero).
    Returns `(po_balance_after, breached, locked_balance)`:
      - `po_balance_after` is the new balance on success, else None;
      - `breached` is True when the invoice no longer fits (caller downgrades);
      - `locked_balance` is the balance observed under the lock (None if no PO row).

    Shared by the auto-decision (`commit_decision`) and the human review-approve
    path so the FOR UPDATE logic lives in exactly one place."""
    cur.execute(
        """SELECT remaining_balance, tolerance_pct
           FROM purchase_orders WHERE po_id = %s FOR UPDATE""",
        (po_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None, False, None
    balance, tol_pct = float(row[0]), float(row[1])
    if _breaches_tolerance(invoice_total, balance, tol_pct):
        return None, True, balance
    new_balance = round(balance - invoice_total, 2)
    new_status = "closed" if new_balance <= _CLOSE_EPSILON else "open"
    cur.execute(
        """UPDATE purchase_orders
           SET remaining_balance = %s, status = %s
           WHERE po_id = %s""",
        (new_balance, new_status, po_id),
    )
    return new_balance, False, balance


def commit_decision(
    verdict: dict,
    matched_po_id: str | None,
    invoice_total: float | None,
    run_id: str | None,
    actor: object | None = None,
) -> dict:
    """Finalise and persist `verdict`. Returns the (possibly downgraded) verdict
    with `po_balance_after` and `decided_at` populated. `actor` (a CurrentUser or
    None) is stamped onto the decision event so the trail records who ran it."""
    actor_label, actor_user_id, actor_role = recorder.actor_fields(actor)
    with cursor(autocommit=False) as cur:
        po_balance_after = None

        if verdict["verdict"] == APPROVE and matched_po_id and invoice_total is not None:
            new_balance, breached, locked_balance = draw_down_po(
                cur, matched_po_id, invoice_total)
            if breached:
                # Concurrent draw-down: downgrade rather than over-commit.
                _downgrade(verdict, locked_balance)
            else:
                po_balance_after = new_balance

        verdict["po_balance_after"] = po_balance_after

        cur.execute(
            """INSERT INTO verdicts
               (run_id, invoice_number, po_reference, verdict, reason, drivers,
                requires_human_review, review_payload, confidence_overall,
                policy_version, auto_approve_ceiling_applied, po_balance_after,
                invoice_total, matched_po_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING decided_at""",
            (
                run_id, verdict["invoice_number"], verdict["po_reference"],
                verdict["verdict"], verdict["reason"], Jsonb(verdict["drivers"]),
                verdict["requires_human_review"],
                Jsonb(verdict["review_payload"]) if verdict["review_payload"] else None,
                verdict["confidence_overall"], verdict["policy_version"],
                verdict["auto_approve_ceiling_applied"], po_balance_after,
                invoice_total, matched_po_id,
            ),
        )
        verdict["decided_at"] = cur.fetchone()[0].isoformat()

        cur.execute(
            """INSERT INTO governance_events
               (run_id, stage, status, detail, actor,
                actor_user_id, actor_role, action_type)
               VALUES (%s, 'decision', %s, %s, %s, %s, %s, 'pipeline_run')""",
            (
                run_id, _EVENT_STATUS.get(verdict["verdict"], "ok"),
                Jsonb({
                    "verdict": verdict["verdict"],
                    "reason": verdict["reason"],
                    "policy_version": verdict["policy_version"],
                    "po_balance_after": po_balance_after,
                }),
                actor_label, actor_user_id, actor_role,
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

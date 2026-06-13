"""Review queue + effectful review actions.

The queue is FLAG verdicts (`requires_human_review`) with no terminal
(approve/reject) action yet — an `escalate` leaves an item visible. Applying an
action records a `review_actions` row and a governance event with the acting
user, all in one transaction; an `approve` additionally draws the matched PO
down via the shared `commit.draw_down_po` (re-using the FOR UPDATE logic so it
can never over-commit).
"""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from app import config
from app.db.connection import cursor
from app.decide.commit import draw_down_po
from app.governance import recorder

APPROVE, REJECT, ESCALATE = "approve", "reject", "escalate"
VALID_ACTIONS = {APPROVE, REJECT, ESCALATE}

# action → (governance stage status, action_type)
_EVENT = {
    APPROVE:  ("ok",   "review_approve"),
    REJECT:   ("fail", "review_reject"),
    ESCALATE: ("warn", "review_escalate"),
}


class ReviewError(Exception):
    """Raised when an action can't be applied (mapped to a 404/409 by the router)."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def review_queue() -> list[dict[str, Any]]:
    """Flagged runs still awaiting a terminal decision, newest first."""
    with cursor() as cur:
        cur.execute(
            """SELECT v.run_id, v.invoice_number, v.po_reference, v.verdict,
                      v.reason, v.review_payload, v.invoice_total, v.matched_po_id,
                      v.decided_at, r.vendor_name, r.actor_user_id
               FROM verdicts v
               JOIN pipeline_runs r ON r.run_id = v.run_id
               WHERE v.tenant_id = %s
                 AND v.requires_human_review = TRUE
                 AND NOT EXISTS (
                     SELECT 1 FROM review_actions ra
                     WHERE ra.run_id = v.run_id AND ra.action IN ('approve', 'reject')
                 )
               ORDER BY v.decided_at DESC""",
            (config.TENANT_ID,),
        )
        rows = cur.fetchall()
    return [
        {
            "run_id": str(run_id),
            "invoice_number": inv,
            "po_reference": po,
            "vendor_name": vendor,
            "verdict": verdict,
            "reason": reason,
            "review_payload": payload,
            "invoice_total": float(total) if total is not None else None,
            "matched_po_id": matched_po,
            "decided_at": decided.isoformat() if decided else None,
        }
        for (run_id, inv, po, verdict, reason, payload, total, matched_po,
             decided, vendor, _owner) in rows
    ]


def apply_review_action(run_id: str, action: str, note: str | None, actor: object) -> dict[str, Any]:
    """Apply a human review decision to a run. Returns a result summary.

    Raises ReviewError(404) if the run has no verdict, ReviewError(409) if an
    `approve` would over-commit the PO (left flagged, nothing written)."""
    if action not in VALID_ACTIONS:
        raise ReviewError(422, f"action must be one of {sorted(VALID_ACTIONS)}")

    actor_label, actor_user_id, actor_role = recorder.actor_fields(actor)
    status, action_type = _EVENT[action]

    with cursor(autocommit=False) as cur:
        cur.execute(
            """SELECT invoice_number, po_reference, verdict, invoice_total,
                      matched_po_id, po_balance_after
               FROM verdicts WHERE run_id = %s AND tenant_id = %s
               ORDER BY decided_at DESC LIMIT 1""",
            (run_id, config.TENANT_ID),
        )
        v = cur.fetchone()
        if v is None:
            raise ReviewError(404, f"No verdict for run {run_id}")
        invoice_number, po_reference, _verdict, invoice_total, matched_po_id, prior_balance = v

        po_balance_after = None
        if action == APPROVE and matched_po_id and invoice_total is not None and prior_balance is None:
            new_balance, breached, _locked = draw_down_po(cur, matched_po_id, float(invoice_total))
            if breached:
                # Would over-commit the PO — leave it flagged, record nothing.
                raise ReviewError(
                    409,
                    "PO balance insufficient to approve — invoice would over-commit "
                    "the PO; left flagged for re-review.",
                )
            po_balance_after = new_balance

        cur.execute(
            """INSERT INTO review_actions
               (run_id, invoice_number, action, note, actor_user_id, actor_role,
                po_balance_after)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               RETURNING review_id, created_at""",
            (run_id, invoice_number, action, note, actor_user_id, actor_role,
             po_balance_after),
        )
        review_id, created_at = cur.fetchone()

        cur.execute(
            """INSERT INTO governance_events
               (run_id, stage, status, detail, actor, actor_user_id, actor_role, action_type)
               VALUES (%s, 'review', %s, %s, %s, %s, %s, %s)""",
            (run_id, status,
             Jsonb({"action": action, "note": note,
                    "po_balance_after": po_balance_after}),
             actor_label, actor_user_id, actor_role, action_type),
        )

    return {
        "review_id": review_id,
        "run_id": run_id,
        "invoice_number": invoice_number,
        "po_reference": po_reference,
        "action": action,
        "po_balance_after": float(po_balance_after) if po_balance_after is not None else None,
        "created_at": created_at.isoformat() if created_at else None,
    }

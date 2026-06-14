"""Raw-SQL reads for run list/detail. Every query is tenant-scoped.

A run's verdict lives in a separate table (`verdicts`, one row per run); these
helpers LEFT JOIN it so a still-running or verdict-less run still lists.
"""
from __future__ import annotations

from typing import Any

from app import config
from app.db.connection import cursor
from app.governance import recorder

MANAGER = "manager"


def list_runs(
    *,
    role: str,
    actor_user_id: str | None,
    verdict: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Run summaries, newest first. Clerks are restricted to their own runs."""
    where = ["r.tenant_id = %s"]
    params: list[Any] = [config.TENANT_ID]
    if role != MANAGER:
        where.append("r.actor_user_id = %s")
        params.append(actor_user_id)
    if verdict:
        where.append("v.verdict = %s")
        params.append(verdict)
    params.extend([limit, offset])

    with cursor() as cur:
        cur.execute(
            f"""SELECT r.run_id, r.invoice_number, r.vendor_name, r.po_reference,
                       r.source_type, r.started_at, r.finished_at,
                       r.actor_user_id, r.actor_role,
                       v.verdict, v.requires_human_review, v.po_balance_after,
                       v.invoice_total, r.overall_conf, la.action
                FROM pipeline_runs r
                LEFT JOIN verdicts v ON v.run_id = r.run_id
                LEFT JOIN LATERAL (
                    SELECT action FROM review_actions ra
                    WHERE ra.run_id = r.run_id AND ra.action IN ('approve', 'reject')
                    ORDER BY ra.created_at DESC LIMIT 1
                ) la ON TRUE
                WHERE {' AND '.join(where)}
                ORDER BY r.started_at DESC
                LIMIT %s OFFSET %s""",
            params,
        )
        return [_summary(row) for row in cur.fetchall()]


def get_run(run_id: str, *, role: str, actor_user_id: str | None) -> dict[str, Any] | None:
    """Full run detail (run + verdict + report summary + events), or None if the
    run doesn't exist in the tenant or a clerk asks for someone else's run."""
    with cursor() as cur:
        cur.execute(
            """SELECT run_id, invoice_number, vendor_name, po_reference, source_type,
                      invoice_path, overall_conf, started_at, finished_at,
                      actor_user_id, actor_role
               FROM pipeline_runs
               WHERE run_id = %s AND tenant_id = %s""",
            (run_id, config.TENANT_ID),
        )
        r = cur.fetchone()
        if r is None:
            return None
        owner_id = str(r[9]) if r[9] else None
        if role != MANAGER and owner_id != actor_user_id:
            return None  # clerks can't read another user's run (404, not 403)

        cur.execute(
            """SELECT verdict, reason, requires_human_review, review_payload,
                      po_balance_after, policy_version, decided_at
               FROM verdicts WHERE run_id = %s
               ORDER BY decided_at DESC LIMIT 1""",
            (run_id,),
        )
        vr = cur.fetchone()

        cur.execute(
            """SELECT passed, failed, skipped FROM validation_reports
               WHERE run_id = %s ORDER BY created_at DESC LIMIT 1""",
            (run_id,),
        )
        rep = cur.fetchone()

    # Events come from the shared recorder helper (also used by the orchestrator
    # to return the trail on the /process response) — one event shape, one place.
    events = recorder.fetch_run_events(run_id)

    return {
        "run_id": str(r[0]),
        "invoice_number": r[1],
        "vendor_name": r[2],
        "po_reference": r[3],
        "source_type": r[4],
        "invoice_path": r[5],
        "overall_conf": float(r[6]) if r[6] is not None else None,
        "started_at": r[7].isoformat() if r[7] else None,
        "finished_at": r[8].isoformat() if r[8] else None,
        "actor_user_id": owner_id,
        "actor_role": r[10],
        "verdict": _verdict(vr),
        "report_summary": (
            {"passed": rep[0], "failed": rep[1], "skipped": rep[2]} if rep else None
        ),
        "events": events,
    }


def _summary(row: tuple) -> dict[str, Any]:
    (run_id, inv, vendor, po, source, started, finished,
     actor_user_id, actor_role, verdict, needs_review, bal_after,
     invoice_total, overall_conf, last_action) = row
    return {
        "run_id": str(run_id),
        "invoice_number": inv,
        "vendor_name": vendor,
        "po_reference": po,
        "source_type": source,
        "verdict": verdict,
        "requires_human_review": needs_review,
        "last_action": last_action,  # latest terminal human action (approve/reject), or None
        "invoice_total": float(invoice_total) if invoice_total is not None else None,
        "overall_conf": float(overall_conf) if overall_conf is not None else None,
        "po_balance_after": float(bal_after) if bal_after is not None else None,
        "actor_user_id": str(actor_user_id) if actor_user_id else None,
        "actor_role": actor_role,
        "started_at": started.isoformat() if started else None,
        "finished_at": finished.isoformat() if finished else None,
    }


def _verdict(vr: tuple | None) -> dict[str, Any] | None:
    if vr is None:
        return None
    return {
        "verdict": vr[0],
        "reason": vr[1],
        "requires_human_review": vr[2],
        "review_payload": vr[3],
        "po_balance_after": float(vr[4]) if vr[4] is not None else None,
        "policy_version": vr[5],
        "decided_at": vr[6].isoformat() if vr[6] else None,
    }

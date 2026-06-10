"""Governance audit trail recorder.

Every pipeline stage emits one or more append-only events, all correlated by a
run_id, so any invoice's full history (ingest -> extract -> match -> validate ->
decision) can be reconstructed for audit.

Audit writes are best-effort: a logging failure logs a warning and returns, it
never raises — recording the trail must not break the pipeline it observes.
Reads (fetch_audit_trail) do raise, since a caller asking for the trail wants to
know if the store is unreachable.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from psycopg.types.json import Jsonb

from app.db.connection import cursor

log = logging.getLogger(__name__)

# Pipeline stages.
INGEST   = "ingest"
EXTRACT  = "extract"
MATCH    = "match"
VALIDATE = "validate"
DECISION = "decision"   # reserved for the next module (decision engine)

# Event statuses.
OK    = "ok"
FAIL  = "fail"
SKIP  = "skip"
WARN  = "warn"
ERROR = "error"


def start_run(
    invoice_path: str = "",
    invoice_number: str | None = None,
    vendor_name: str | None = None,
    po_reference: str | None = None,
    source_type: str | None = None,
) -> str:
    """Create a pipeline_runs row and return its run_id (UUID string).

    Best-effort: returns a fresh UUID even if the insert fails, so the caller
    always has a correlation id to thread through the rest of the run.
    """
    run_id = str(uuid.uuid4())
    try:
        with cursor() as cur:
            cur.execute(
                """INSERT INTO pipeline_runs
                   (run_id, invoice_number, vendor_name, po_reference,
                    source_type, invoice_path)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (run_id, invoice_number, vendor_name, po_reference,
                 source_type, invoice_path),
            )
    except Exception as exc:  # noqa: BLE001 — audit must not break the pipeline
        log.warning("governance start_run failed: %s", exc)
    return run_id


def log_event(
    run_id: str | None,
    stage: str,
    status: str,
    detail: dict[str, Any] | None = None,
    actor: str = "system",
) -> None:
    """Append one governance event. Best-effort — never raises."""
    if run_id is None:
        return
    try:
        with cursor() as cur:
            cur.execute(
                """INSERT INTO governance_events
                   (run_id, stage, status, detail, actor)
                   VALUES (%s, %s, %s, %s, %s)""",
                (run_id, stage, status,
                 Jsonb(detail) if detail is not None else None, actor),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("governance log_event(%s/%s) failed: %s", stage, status, exc)


def update_run(
    run_id: str | None,
    invoice_number: str | None = None,
    vendor_name: str | None = None,
    po_reference: str | None = None,
    source_type: str | None = None,
    overall_conf: float | None = None,
) -> None:
    """Backfill run metadata learned after start_run (e.g. post-extraction)."""
    if run_id is None:
        return
    sets, vals = [], []
    for col, val in (
        ("invoice_number", invoice_number),
        ("vendor_name", vendor_name),
        ("po_reference", po_reference),
        ("source_type", source_type),
        ("overall_conf", overall_conf),
    ):
        if val is not None:
            sets.append(f"{col} = %s")
            vals.append(val)
    if not sets:
        return
    vals.append(run_id)
    try:
        with cursor() as cur:
            cur.execute(
                f"UPDATE pipeline_runs SET {', '.join(sets)} WHERE run_id = %s",
                vals,
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("governance update_run failed: %s", exc)


def finish_run(run_id: str | None, overall_conf: float | None = None) -> None:
    """Mark a run finished. Best-effort — never raises."""
    if run_id is None:
        return
    try:
        with cursor() as cur:
            if overall_conf is not None:
                cur.execute(
                    "UPDATE pipeline_runs SET finished_at = now(), overall_conf = %s "
                    "WHERE run_id = %s",
                    (overall_conf, run_id),
                )
            else:
                cur.execute(
                    "UPDATE pipeline_runs SET finished_at = now() WHERE run_id = %s",
                    (run_id,),
                )
    except Exception as exc:  # noqa: BLE001
        log.warning("governance finish_run failed: %s", exc)


def record_validation_report(
    run_id: str | None,
    invoice_number: str | None,
    report: dict[str, Any],
) -> None:
    """Persist a full validation report (JSONB). Best-effort — never raises."""
    summary = report.get("summary") or {}
    try:
        with cursor() as cur:
            cur.execute(
                """INSERT INTO validation_reports
                   (run_id, invoice_number, report, passed, failed, skipped)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (run_id, invoice_number, Jsonb(report),
                 summary.get("passed"), summary.get("failed"), summary.get("skipped")),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("governance record_validation_report failed: %s", exc)


def fetch_audit_trail(invoice_number: str) -> dict[str, Any]:
    """Reconstruct the audit trail for an invoice across all its runs.

    Returns {invoice_number, runs: [{run_id, started_at, finished_at, events: [...]}],
             latest_report}. Raises if the store is unreachable.
    """
    with cursor() as cur:
        cur.execute(
            """SELECT run_id, source_type, invoice_path, overall_conf,
                      started_at, finished_at
               FROM pipeline_runs
               WHERE invoice_number = %s
               ORDER BY started_at""",
            (invoice_number,),
        )
        run_rows = cur.fetchall()

        runs = []
        for (run_id, source_type, invoice_path, overall_conf,
             started_at, finished_at) in run_rows:
            cur.execute(
                """SELECT stage, status, detail, actor, created_at
                   FROM governance_events
                   WHERE run_id = %s
                   ORDER BY event_id""",
                (run_id,),
            )
            events = [
                {
                    "stage": stage, "status": status, "detail": detail,
                    "actor": actor,
                    "ts": created_at.isoformat() if created_at else None,
                }
                for (stage, status, detail, actor, created_at) in cur.fetchall()
            ]
            runs.append({
                "run_id": str(run_id),
                "source_type": source_type,
                "invoice_path": invoice_path,
                "overall_conf": float(overall_conf) if overall_conf is not None else None,
                "started_at": started_at.isoformat() if started_at else None,
                "finished_at": finished_at.isoformat() if finished_at else None,
                "events": events,
            })

        cur.execute(
            """SELECT report FROM validation_reports
               WHERE invoice_number = %s
               ORDER BY created_at DESC
               LIMIT 1""",
            (invoice_number,),
        )
        row = cur.fetchone()
        latest_report = row[0] if row else None

        cur.execute(
            """SELECT verdict, reason, drivers, requires_human_review,
                      review_payload, confidence_overall, policy_version,
                      auto_approve_ceiling_applied, po_balance_after, decided_at
               FROM verdicts
               WHERE invoice_number = %s
               ORDER BY decided_at DESC
               LIMIT 1""",
            (invoice_number,),
        )
        v = cur.fetchone()

    latest_verdict = None
    if v:
        latest_verdict = {
            "verdict": v[0], "reason": v[1], "drivers": v[2],
            "requires_human_review": v[3], "review_payload": v[4],
            "confidence_overall": float(v[5]) if v[5] is not None else None,
            "policy_version": v[6],
            "auto_approve_ceiling_applied": float(v[7]) if v[7] is not None else None,
            "po_balance_after": float(v[8]) if v[8] is not None else None,
            "decided_at": v[9].isoformat() if v[9] else None,
        }

    return {
        "invoice_number": invoice_number,
        "runs": runs,
        "latest_report": latest_report,
        "latest_verdict": latest_verdict,
    }

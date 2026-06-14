"""Tenant-scoped aggregate reads for the manager dashboard."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app import config
from app.db.connection import cursor

_VERDICTS = ("APPROVE", "FLAG", "REJECT")


def summary() -> dict[str, Any]:
    """Verdict counts, the review backlog depth, and total runs."""
    with cursor() as cur:
        cur.execute(
            "SELECT verdict, count(*) FROM verdicts WHERE tenant_id = %s GROUP BY verdict",
            (config.TENANT_ID,),
        )
        counts = {v: 0 for v in _VERDICTS}
        for verdict, n in cur.fetchall():
            counts[verdict] = n

        cur.execute(
            """SELECT count(*) FROM verdicts v
               WHERE v.tenant_id = %s AND v.requires_human_review = TRUE
                 AND NOT EXISTS (
                     SELECT 1 FROM review_actions ra
                     WHERE ra.run_id = v.run_id AND ra.action IN ('approve', 'reject')
                 )""",
            (config.TENANT_ID,),
        )
        needs_review = cur.fetchone()[0]

        cur.execute(
            "SELECT count(*) FROM pipeline_runs WHERE tenant_id = %s",
            (config.TENANT_ID,),
        )
        total_runs = cur.fetchone()[0]

    return {
        "verdicts": counts,
        "needs_review": needs_review,
        "total_runs": total_runs,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


def trends(days: int = 30) -> dict[str, Any]:
    """Per-day verdict buckets over the last `days` days (most recent last)."""
    with cursor() as cur:
        cur.execute(
            """SELECT date_trunc('day', decided_at)::date AS d, verdict, count(*)
               FROM verdicts
               WHERE tenant_id = %s
                 AND decided_at >= now() - make_interval(days => %s)
               GROUP BY d, verdict
               ORDER BY d""",
            (config.TENANT_ID, days),
        )
        rows = cur.fetchall()

    buckets: dict[str, dict[str, Any]] = {}
    for d, verdict, n in rows:
        key = d.isoformat()
        bucket = buckets.setdefault(
            key, {"date": key, "APPROVE": 0, "FLAG": 0, "REJECT": 0, "total": 0}
        )
        bucket[verdict] = n
        bucket["total"] += n

    return {"days": days, "trends": list(buckets.values())}


# --------------------------------------------------------------------------- #
# Headline KPIs (handover §6.1–6.2) — one payload, all computed server-side so
# the UI does zero arithmetic. Quality KPIs (false-approve / override) are
# deliberately omitted (§6.6); the UI renders an honest placeholder for them.
# --------------------------------------------------------------------------- #

# A REJECT's reject-severity driver signal → the stable reason key the dashboard
# groups by (human labels live in the UI).
_REJECT_REASON = {
    "po_lookup":       "po_not_found",
    "vendor_approved": "unapproved_vendor",
    "po_status":       "closed_po",
    "duplicate":       "duplicate",
}


def _costs(cur) -> tuple[float, float]:
    """(manual_cost_per_invoice, auto_cost_per_invoice) from policy_config."""
    cur.execute(
        "SELECT coalesce(manual_cost_per_invoice, 0), coalesce(auto_cost_per_invoice, 0) "
        "FROM policy_config WHERE id = 1"
    )
    row = cur.fetchone()
    return (float(row[0]), float(row[1])) if row else (0.0, 0.0)


def _window_metrics(cur, start, end, manual_cost: float, auto_cost: float) -> dict[str, Any]:
    """Compute every scalar KPI + the two reason breakdowns for one time window."""
    # One pass over the window's verdicts powers counts, savings, duplicate
    # prevented, and the flags/rejections breakdowns.
    cur.execute(
        """SELECT verdict, drivers, review_payload
           FROM verdicts
           WHERE tenant_id = %s AND decided_at >= %s AND decided_at < %s""",
        (config.TENANT_ID, start, end),
    )
    approve = flag = reject = 0
    flags_by_reason: dict[str, int] = {}
    rejections_by_reason: dict[str, int] = {}
    for verdict, drivers, payload in cur.fetchall():
        if verdict == "APPROVE":
            approve += 1
        elif verdict == "FLAG":
            flag += 1
            queue = (payload or {}).get("queue") or "other"
            flags_by_reason[queue] = flags_by_reason.get(queue, 0) + 1
        elif verdict == "REJECT":
            reject += 1
            reject_signals = {
                d.get("signal") for d in (drivers or [])
                if d.get("severity") == "REJECT"
            }
            for sig in reject_signals:
                key = _REJECT_REASON.get(sig, sig)
                rejections_by_reason[key] = rejections_by_reason.get(key, 0) + 1
    total = approve + flag + reject

    # Cycle time (ms) over runs started in the window.
    cur.execute(
        """SELECT avg(extract(epoch FROM (finished_at - started_at))) * 1000
           FROM pipeline_runs
           WHERE tenant_id = %s AND finished_at IS NOT NULL
             AND started_at >= %s AND started_at < %s""",
        (config.TENANT_ID, start, end),
    )
    avg_cycle_ms = cur.fetchone()[0]

    # Time-in-queue (sec): flagged verdict → its first terminal review action.
    cur.execute(
        """SELECT avg(extract(epoch FROM (t.first_action - v.decided_at)))
           FROM verdicts v
           JOIN (SELECT run_id, min(created_at) AS first_action
                 FROM review_actions WHERE action IN ('approve', 'reject')
                 GROUP BY run_id) t ON t.run_id = v.run_id
           WHERE v.tenant_id = %s AND v.requires_human_review = TRUE
             AND v.decided_at >= %s AND v.decided_at < %s""",
        (config.TENANT_ID, start, end),
    )
    avg_queue_sec = cur.fetchone()[0]

    # Audit completeness: runs whose events cover the full stage chain ÷ total.
    cur.execute(
        """SELECT count(*) AS total,
                  count(*) FILTER (
                     WHERE stages @> ARRAY['ingest','extract','match','validate','decision']
                  ) AS complete
           FROM (
              SELECT r.run_id, array_agg(DISTINCT e.stage) AS stages
              FROM pipeline_runs r
              LEFT JOIN governance_events e ON e.run_id = r.run_id
              WHERE r.tenant_id = %s AND r.started_at >= %s AND r.started_at < %s
              GROUP BY r.run_id
           ) sub""",
        (config.TENANT_ID, start, end),
    )
    runs_total, runs_complete = cur.fetchone()

    return {
        "total": total, "approve": approve, "flag": flag, "reject": reject,
        "stp_rate": (approve / total) if total else None,
        "avg_cycle_ms": float(avg_cycle_ms) if avg_cycle_ms is not None else None,
        "avg_time_in_queue_sec": float(avg_queue_sec) if avg_queue_sec is not None else None,
        "touchless_savings": approve * (manual_cost - auto_cost),
        "audit_completeness": (runs_complete / runs_total) if runs_total else None,
        "flags_by_reason": flags_by_reason,
        "rejections_by_reason": rejections_by_reason,
    }


def _delta(cur_val: float | None, prev_val: float | None) -> float | None:
    """Signed change vs the prior period, or None when either side is absent."""
    if cur_val is None or prev_val is None:
        return None
    return cur_val - prev_val


def kpis(days: int = 30) -> dict[str, Any]:
    """Headline KPIs over the last `days` days, each with a delta vs the prior
    equal-length period (None where no prior data exists)."""
    now = datetime.now(timezone.utc)
    cur_start = now - timedelta(days=days)
    prev_start = now - timedelta(days=2 * days)
    with cursor() as cur:
        manual_cost, auto_cost = _costs(cur)
        current = _window_metrics(cur, cur_start, now, manual_cost, auto_cost)
        previous = _window_metrics(cur, prev_start, cur_start, manual_cost, auto_cost)

    def card(key: str) -> dict[str, Any]:
        return {"value": current[key], "delta": _delta(current[key], previous[key])}

    return {
        "as_of": now.isoformat(),
        "window_days": days,
        "totals": {
            "verdicts": current["total"], "approve": current["approve"],
            "flag": current["flag"], "reject": current["reject"],
        },
        "kpis": {
            "stp_rate": card("stp_rate"),
            "avg_cycle_ms": card("avg_cycle_ms"),
            "avg_time_in_queue_sec": card("avg_time_in_queue_sec"),
            "touchless_savings": card("touchless_savings"),
            "audit_completeness": card("audit_completeness"),
        },
        "flags_by_reason": current["flags_by_reason"],
        "rejections_by_reason": current["rejections_by_reason"],
        "costs": {"manual_cost_per_invoice": manual_cost,
                  "auto_cost_per_invoice": auto_cost},
    }

"""Tenant-scoped aggregate reads for the manager dashboard."""
from __future__ import annotations

from datetime import datetime, timezone
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

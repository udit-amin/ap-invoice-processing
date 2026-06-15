"""Demo-data reset — clear all processed runs and restore reference data to its
baseline, leaving a **clean slate**.

The demo demonstrates everything live: a straight-through batch (APPROVE/REJECT),
then the edge cases one at a time. So the system starts empty — no pre-seeded runs.
Reseeding reference data restores every PO (reopens closed/drawn-down POs), so the
demo is repeatable.

Demo-only and deliberately destructive — no model calls, so it runs on the deployed
container with no Anthropic key. This is the single source of truth for the reset;
`scripts/seed_demo_history.py` is a thin CLI wrapper and `POST /admin/reset-demo`
calls it behind a role + env gate.
"""
from __future__ import annotations

from app.db import seed as _seed
from app.db.connection import cursor


def reset_demo_data() -> dict:
    """Truncate operational tables and restore reference data (vendors, POs, policy)
    to baseline. Users are untouched. Returns a small summary."""
    _seed.seed()  # reference data + policy (incl. cost constants); restores PO balances
    with cursor() as cur:
        cur.execute(
            "TRUNCATE invoice_files, review_actions, governance_events, "
            "validation_reports, verdicts, invoices, pipeline_runs "
            "RESTART IDENTITY CASCADE"
        )
    return {"runs": 0, "tally": {}}

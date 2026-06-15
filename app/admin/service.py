"""Demo-data reset — truncate operational tables and re-seed a few days of
back-dated runs so the dashboard, review queue, and trend look populated.

Demo-only and deliberately destructive. It reuses the **answer-key** pipeline
(no model calls), so it runs on the deployed container (fixtures + answer key are
baked into the image) with no Anthropic key. The 11 fixtures are each processed
once with PO balances restored before each, so the verdict mix stays
6 APPROVE / 3 FLAG / 2 REJECT — spread across recent days.

This is the single source of truth for the reset; `scripts/seed_demo_history.py`
is a thin CLI wrapper and `POST /admin/reset-demo` calls it behind a role + env gate.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app import config
from app.db import seed as _seed
from app.db.connection import cursor
from app.decide import commit, engine
from app.decide.policy import load_policy
from app.governance import recorder
from app.validate.validator import validate
from validate_all import (
    ANSWER_KEY_PATH, _answer_key_to_extracted, _reset_po_balances,
)

# fixture → days-ago (spread across ~5 days; the flags land recent so they
# populate the live review queue, oldest-first).
_SCHEDULE = {
    "normal_1_dell.pdf": 4,
    "normal_2_stellar.pdf": 4,
    "normal_3_fastfreight.pdf": 3,
    "normal_4_apex.pdf": 3,
    "normal_5_nimbus.pdf": 2,
    "edge_3_blueprint_embedded_tax.pdf": 2,
    "edge_1_greenleaf_scanned.pdf": 1,
    "edge_6_cloudhost_closed_po.pdf": 1,
    "edge_2_techgear_bundled.pdf": 0,
    "edge_4_dell_line_mismatch.pdf": 0,
    "edge_5_globex_unapproved.pdf": 0,
}


def _truncate_and_reseed_reference() -> None:
    _seed.seed()  # reference data + policy (incl. cost constants)
    with cursor() as cur:
        cur.execute(
            "TRUNCATE invoice_files, review_actions, governance_events, "
            "validation_reports, verdicts, invoices, pipeline_runs "
            "RESTART IDENTITY CASCADE"
        )


def _process(filename: str, extracted: dict, when: datetime) -> str:
    overall = (extracted.get("extraction_confidence") or {}).get("overall")
    run_id = recorder.start_run(
        invoice_path=filename,
        invoice_number=extracted.get("invoice_number"),
        vendor_name=extracted.get("vendor_name"),
        po_reference=extracted.get("po_reference"),
        source_type=extracted.get("source_type"),
        actor_user_id=None, actor_role=None,  # harness = system actor
    )
    pdf = config.INPUTS_DIR / filename
    if pdf.exists():
        recorder.store_invoice_file(run_id, pdf.read_bytes(), filename=filename)
    recorder.store_extraction(run_id, extracted)
    recorder.log_event(run_id, recorder.INGEST, recorder.OK, {"invoice_path": filename})
    recorder.log_event(run_id, recorder.EXTRACT, recorder.OK,
                       {"source_type": extracted.get("source_type"), "overall_conf": overall})
    report = validate(extracted, run_id=run_id)
    verdict = engine.decide(report, extracted, load_policy())
    commit.commit_decision(verdict, report.get("matched_po"), extracted.get("total"), run_id)
    recorder.finish_run(run_id, overall_conf=overall)

    # Back-date everything for this run so the trend spans several days.
    with cursor() as cur:
        cur.execute("UPDATE pipeline_runs SET started_at=%s, finished_at=%s WHERE run_id=%s",
                    (when, when, run_id))
        cur.execute("UPDATE verdicts SET decided_at=%s WHERE run_id=%s", (when, run_id))
        cur.execute("UPDATE governance_events SET created_at=%s WHERE run_id=%s", (when, run_id))
    return verdict["verdict"]


def reset_demo_data() -> dict:
    """Wipe operational data and re-seed the back-dated demo history.

    Returns {"runs": N, "tally": {APPROVE, FLAG, REJECT}, "days": D}.
    """
    if not ANSWER_KEY_PATH.exists():
        raise RuntimeError(f"Answer key not found at {ANSWER_KEY_PATH}")
    answer_key = json.loads(ANSWER_KEY_PATH.read_text())
    _truncate_and_reseed_reference()

    now = datetime.now(timezone.utc)
    tally: dict[str, int] = {}
    for filename, days_ago in _SCHEDULE.items():
        _reset_po_balances()  # judge each invoice against the seeded baseline
        extracted = _answer_key_to_extracted(filename, answer_key)
        when = now - timedelta(days=days_ago, hours=days_ago, minutes=len(tally))
        v = _process(filename, extracted, when)
        tally[v] = tally.get(v, 0) + 1

    return {"runs": sum(tally.values()), "tally": tally, "days": len(set(_SCHEDULE.values()))}

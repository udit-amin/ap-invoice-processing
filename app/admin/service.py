"""Demo-data reset — truncate operational tables and re-seed the **starting state**
for a demo: exactly two flagged runs already awaiting review.

The demo then builds up live — a straight-through batch (APPROVE/REJECT) and a few
edge-case uploads (FLAGs) — so the queue and dashboard fill in on camera. This seeds
only the two flags so the system doesn't start empty.

Demo-only and deliberately destructive. It reuses the **answer-key** pipeline (no
model calls), so it runs on the deployed container (fixtures + answer key are baked
into the image) with no Anthropic key. Reference data is reseeded, which restores
every PO to its baseline (reopens closed/drawn-down POs), so the demo is repeatable.

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

def _truncate_and_reseed_reference() -> None:
    _seed.seed()  # reference data + policy (incl. cost constants)
    with cursor() as cur:
        cur.execute(
            "TRUNCATE invoice_files, review_actions, governance_events, "
            "validation_reports, verdicts, invoices, pipeline_runs "
            "RESTART IDENTITY CASCADE"
        )


def _missing_tax_seed_case() -> tuple[dict, bytes]:
    """A synthetic FastFreight / PO-5011 invoice with no tax → missing-tax FLAG.

    The answer key never models tax (treatment is null → tax_present skips), so the
    seed builds this extraction directly and renders a matching PDF to store. Every
    other check passes (PO-5011 is stored ex-tax), so the only flag is tax_present.
    """
    from app.generate import invoice_generator as gen
    from app.generate.invoice_generator import InvoiceSpec, LineItem, _render_pdf_bytes
    gen._VENDOR_META.setdefault("FastFreight Logistics", ("24CCCCC0002C1Z4", "Net-45"))
    spec = InvoiceSpec("demo_seed_missing_tax.pdf", "FastFreight Logistics", "FF-2026-0588",
                       "2026-06-12", "PO-5011",
                       [LineItem("Inter-state freight (zero-rated)", 1, 200000.0, 200000.0)],
                       "none", None, subtotal=200000.0, tax_amount=None, total=200000.0)
    extraction = {
        "source_type": "text", "invoice_number": "FF-2026-0588",
        "vendor_name": "FastFreight Logistics", "invoice_date": "2026-06-12",
        "po_reference": "PO-5011", "currency": "INR",
        "line_items": [{"description": "Inter-state freight (zero-rated)", "quantity": 1,
                        "unit_price": 200000, "line_total": 200000,
                        "is_bundle": False, "bundle_components": []}],
        "subtotal": 200000,
        "tax": {"amount": 0, "rate_pct": None, "treatment": "none"},
        "total": 200000,
        "extraction_confidence": {"invoice_number": 0.97, "vendor_name": 0.96,
                                  "po_reference": 0.95, "total": 0.96, "overall": 0.96},
        "extraction_notes": [], "error": None,
    }
    return extraction, _render_pdf_bytes(spec)


def _process(filename: str, extracted: dict, when: datetime,
             pdf_bytes: bytes | None = None) -> str:
    overall = (extracted.get("extraction_confidence") or {}).get("overall")
    run_id = recorder.start_run(
        invoice_path=filename,
        invoice_number=extracted.get("invoice_number"),
        vendor_name=extracted.get("vendor_name"),
        po_reference=extracted.get("po_reference"),
        source_type=extracted.get("source_type"),
        actor_user_id=None, actor_role=None,  # harness = system actor
    )
    if pdf_bytes is None:
        pdf = config.INPUTS_DIR / filename
        pdf_bytes = pdf.read_bytes() if pdf.exists() else None
    if pdf_bytes:
        recorder.store_invoice_file(run_id, pdf_bytes, filename=filename)
    recorder.store_extraction(run_id, extracted)
    recorder.log_event(run_id, recorder.INGEST, recorder.OK, {"invoice_path": filename})
    recorder.log_event(run_id, recorder.EXTRACT, recorder.OK,
                       {"source_type": extracted.get("source_type"), "overall_conf": overall})
    report = validate(extracted, run_id=run_id)
    verdict = engine.decide(report, extracted, load_policy())
    commit.commit_decision(verdict, report.get("matched_po"), extracted.get("total"), run_id)
    recorder.finish_run(run_id, overall_conf=overall)

    # Back-date everything for this run so it reads as aging work.
    with cursor() as cur:
        cur.execute("UPDATE pipeline_runs SET started_at=%s, finished_at=%s WHERE run_id=%s",
                    (when, when, run_id))
        cur.execute("UPDATE verdicts SET decided_at=%s WHERE run_id=%s", (when, run_id))
        cur.execute("UPDATE governance_events SET created_at=%s WHERE run_id=%s", (when, run_id))
    return verdict["verdict"]


def reset_demo_data() -> dict:
    """Wipe operational data and re-seed the demo's starting state: two flagged
    runs already awaiting review — a line-variance side-by-side and a missing-tax
    invoice. Returns {"runs": N, "tally": {...}, "days": D}.
    """
    if not ANSWER_KEY_PATH.exists():
        raise RuntimeError(f"Answer key not found at {ANSWER_KEY_PATH}")
    answer_key = json.loads(ANSWER_KEY_PATH.read_text())
    _truncate_and_reseed_reference()

    now = datetime.now(timezone.utc)
    tally: dict[str, int] = {}

    def _tally(v: str) -> None:
        tally[v] = tally.get(v, 0) + 1

    # Flag 1 — line-variance (Dell), from the answer key.
    _reset_po_balances()
    _tally(_process("edge_4_dell_line_mismatch.pdf",
                    _answer_key_to_extracted("edge_4_dell_line_mismatch.pdf", answer_key),
                    now - timedelta(days=2, hours=2)))

    # Flag 2 — missing tax (FastFreight), synthetic (the answer key has no tax).
    _reset_po_balances()
    extraction, pdf_bytes = _missing_tax_seed_case()
    _tally(_process("demo_seed_missing_tax.pdf", extraction,
                    now - timedelta(days=1, hours=1), pdf_bytes=pdf_bytes))

    return {"runs": sum(tally.values()), "tally": tally, "days": 2}

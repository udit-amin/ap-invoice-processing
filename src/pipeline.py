"""End-to-end pipeline orchestrator: ingest -> extract -> match -> validate.

Used by both the API (`POST /process`) and the harness (`validate_all.py`) so the
governance audit trail is identical no matter how an invoice enters the system.
The validator emits the match/validate events itself; this module emits the
ingest and extract events and brackets the run.
"""
from __future__ import annotations

from src.extract.extractor import extract
from src.validate.validator import validate
from src.governance import recorder
from src.decide import engine, commit
from src.decide.policy import load_policy


def process_invoice(pdf_path: str, invoice_path_label: str | None = None) -> dict:
    """Run a PDF through extraction and validation under one governance run.

    Returns {run_id, extraction, validation}. Never raises on governance
    failures (those are best-effort); extraction/validation already return
    structured payloads rather than raising.
    """
    label = invoice_path_label or pdf_path
    run_id = recorder.start_run(invoice_path=label)
    recorder.log_event(run_id, recorder.INGEST, recorder.OK,
                       detail={"invoice_path": label})

    extraction = extract(pdf_path)

    conf = (extraction.get("extraction_confidence") or {}).get("overall")
    if extraction.get("error"):
        recorder.log_event(run_id, recorder.EXTRACT, recorder.ERROR,
                           detail={"source_type": extraction.get("source_type"),
                                   "error": extraction.get("error")})
    else:
        recorder.log_event(run_id, recorder.EXTRACT, recorder.OK,
                           detail={"source_type": extraction.get("source_type"),
                                   "overall_conf": conf})

    recorder.update_run(
        run_id,
        invoice_number=extraction.get("invoice_number"),
        vendor_name=extraction.get("vendor_name"),
        po_reference=extraction.get("po_reference"),
        source_type=extraction.get("source_type"),
        overall_conf=conf,
    )

    validation = validate(extraction, run_id=run_id)

    # Decide: evidence + confidence + policy → verdict (the one place a verdict
    # is written). commit_decision persists it and, on APPROVE, decrements the
    # PO balance in a race-safe transaction.
    policy = load_policy()
    verdict = engine.decide(validation, extraction, policy)
    decision = commit.commit_decision(
        verdict,
        matched_po_id=validation.get("matched_po"),
        invoice_total=extraction.get("total"),
        run_id=run_id,
    )

    recorder.finish_run(run_id, overall_conf=conf)
    return {
        "run_id": run_id,
        "extraction": extraction,
        "validation": validation,
        "decision": decision,
    }

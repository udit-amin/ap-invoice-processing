"""End-to-end pipeline orchestrator: ingest -> extract -> match -> validate.

Used by both the API (`POST /invoices/process`) and the harness
(`validate_all.py`) so the governance audit trail is identical no matter how an
invoice enters the system. The validator emits the match/validate events itself;
this module emits the ingest and extract events and brackets the run.

`actor` (an auth.CurrentUser or None) records who ran the pipeline; the harness
passes None and everything is stamped as the system actor.
"""
from __future__ import annotations

from pathlib import Path

from app.extract.extractor import extract
from app.validate.validator import validate
from app.governance import recorder
from app.decide import engine, commit
from app.decide.policy import load_policy


def process_invoice(
    pdf_path: str,
    invoice_path_label: str | None = None,
    actor: object | None = None,
) -> dict:
    """Run a PDF through extraction and validation under one governance run.

    Returns {run_id, extraction, validation, decision, events}. Never raises on
    governance failures (those are best-effort); extraction/validation already
    return structured payloads rather than raising.
    """
    actor_label, actor_user_id, actor_role = recorder.actor_fields(actor)
    label = invoice_path_label or pdf_path
    run_id = recorder.start_run(invoice_path=label,
                                actor_user_id=actor_user_id, actor_role=actor_role)
    # Persist the original upload so the review UI can show the source PDF later.
    # Best-effort: a read/storage failure must not break the run.
    try:
        _file_bytes = Path(pdf_path).read_bytes()
    except OSError:
        _file_bytes = None
    recorder.store_invoice_file(run_id, _file_bytes, filename=label)
    recorder.log_event(run_id, recorder.INGEST, recorder.OK,
                       detail={"invoice_path": label}, actor=actor_label,
                       actor_user_id=actor_user_id, actor_role=actor_role,
                       action_type="pipeline_run")

    extraction = extract(pdf_path)

    conf = (extraction.get("extraction_confidence") or {}).get("overall")
    _ev = dict(actor=actor_label, actor_user_id=actor_user_id,
               actor_role=actor_role, action_type="pipeline_run")
    if extraction.get("error"):
        recorder.log_event(run_id, recorder.EXTRACT, recorder.ERROR,
                           detail={"source_type": extraction.get("source_type"),
                                   "error": extraction.get("error")}, **_ev)
    else:
        recorder.log_event(run_id, recorder.EXTRACT, recorder.OK,
                           detail={"source_type": extraction.get("source_type"),
                                   "overall_conf": conf}, **_ev)

    recorder.update_run(
        run_id,
        invoice_number=extraction.get("invoice_number"),
        vendor_name=extraction.get("vendor_name"),
        po_reference=extraction.get("po_reference"),
        source_type=extraction.get("source_type"),
        overall_conf=conf,
    )
    # Persist the full extracted payload (incl. per-field confidence) so the
    # review UI can show extracted values and mark low-confidence fields later.
    recorder.store_extraction(run_id, extraction)

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
        actor=actor,
    )

    recorder.finish_run(run_id, overall_conf=conf)
    return {
        "run_id": run_id,
        "extraction": extraction,
        "validation": validation,
        "decision": decision,
        # The append-only trail this run emitted, oldest first — lets the UI
        # replay the real stage events for the live run view (handover §4.2).
        "events": recorder.fetch_run_events(run_id),
    }

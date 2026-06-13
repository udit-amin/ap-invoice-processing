"""Pipeline HTTP routes: PDF upload → extraction / full pipeline.

Both routes are clerk-only (managers review, they don't process — handover §2.4).
Failed extractions still return 200 with a structured error payload so callers
always get the same shape; the ``error`` field distinguishes success from failure.

The authenticated clerk is threaded through as the run's actor so the governance
trail records *who* processed each invoice (PR1 enforced who may call; PR2 records
who did).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.auth.dependencies import CurrentUser, require_role
from app.extract.extractor import extract
from app.governance import recorder
from app.pipeline import process_invoice

router = APIRouter(tags=["pipeline"])


@router.post("/extract")
async def extract_invoice(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_role("clerk")),
):
    """Upload a PDF → structured extraction JSON (no validation/verdict)."""
    actor_label, actor_user_id, actor_role = recorder.actor_fields(user)
    tmp_path = await _save_upload(file)
    try:
        run_id = recorder.start_run(invoice_path=file.filename,
                                    actor_user_id=actor_user_id, actor_role=actor_role)
        recorder.log_event(run_id, recorder.INGEST, recorder.OK,
                           detail={"invoice_path": file.filename}, actor=actor_label,
                           actor_user_id=actor_user_id, actor_role=actor_role,
                           action_type="pipeline_run")
        result = extract(str(tmp_path))
        _log_extract_event(run_id, result, actor_label, actor_user_id, actor_role)
        recorder.update_run(
            run_id,
            invoice_number=result.get("invoice_number"),
            vendor_name=result.get("vendor_name"),
            po_reference=result.get("po_reference"),
            source_type=result.get("source_type"),
            overall_conf=(result.get("extraction_confidence") or {}).get("overall"),
        )
        recorder.finish_run(run_id)
    finally:
        tmp_path.unlink(missing_ok=True)
    return JSONResponse(content=result)


@router.post("/invoices/process")
async def process_invoice_route(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_role("clerk")),
):
    """Upload a PDF → full pipeline (extraction + validation + verdict).

    Renamed from ``POST /process`` in PR2; the verdict is stamped with the
    acting clerk as the run's actor.
    """
    tmp_path = await _save_upload(file)
    try:
        result = process_invoice(str(tmp_path), invoice_path_label=file.filename,
                                 actor=user)
    finally:
        tmp_path.unlink(missing_ok=True)
    return JSONResponse(content=result)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
async def _save_upload(file: UploadFile) -> Path:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Only PDF files are accepted.")
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(contents)
        return Path(tmp.name)


def _log_extract_event(run_id: str, result: dict, actor_label: str,
                       actor_user_id: str | None, actor_role: str | None) -> None:
    conf = (result.get("extraction_confidence") or {}).get("overall")
    ev = dict(actor=actor_label, actor_user_id=actor_user_id,
              actor_role=actor_role, action_type="pipeline_run")
    if result.get("error"):
        recorder.log_event(run_id, recorder.EXTRACT, recorder.ERROR,
                           detail={"source_type": result.get("source_type"),
                                   "error": result.get("error")}, **ev)
    else:
        recorder.log_event(run_id, recorder.EXTRACT, recorder.OK,
                           detail={"source_type": result.get("source_type"),
                                   "overall_conf": conf}, **ev)

"""Pipeline HTTP routes: PDF upload → extraction / full pipeline.

Both routes are clerk-only (managers review, they don't process — handover §2.4).
Failed extractions still return 200 with a structured error payload so callers
always get the same shape; the ``error`` field distinguishes success from failure.
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
    tmp_path = await _save_upload(file)
    try:
        run_id = recorder.start_run(invoice_path=file.filename)
        recorder.log_event(run_id, recorder.INGEST, recorder.OK,
                           detail={"invoice_path": file.filename})
        result = extract(str(tmp_path))
        _log_extract_event(run_id, result)
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


@router.post("/process")
async def process_invoice_route(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_role("clerk")),
):
    """Upload a PDF → full pipeline (extraction + validation + verdict)."""
    tmp_path = await _save_upload(file)
    try:
        result = process_invoice(str(tmp_path), invoice_path_label=file.filename)
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


def _log_extract_event(run_id: str, result: dict) -> None:
    conf = (result.get("extraction_confidence") or {}).get("overall")
    if result.get("error"):
        recorder.log_event(run_id, recorder.EXTRACT, recorder.ERROR,
                           detail={"source_type": result.get("source_type"),
                                   "error": result.get("error")})
    else:
        recorder.log_event(run_id, recorder.EXTRACT, recorder.OK,
                           detail={"source_type": result.get("source_type"),
                                   "overall_conf": conf})

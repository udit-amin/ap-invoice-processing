"""FastAPI extraction + validation service.

Routes
------
GET  /health              liveness check
POST /extract             upload a PDF → structured extraction JSON
POST /process             upload a PDF → extraction + validation evidence (full pipeline)
GET  /audit/{invoice_no}  read the append-only governance trail for an invoice

Every extraction and every pipeline run is recorded to the governance audit
trail (best-effort: a logging failure never breaks the response). Failed
extractions still return a 200 with the structured error payload so callers
always get the same shape — the ``error`` field distinguishes success from
failure.

Start the server:
    uvicorn src.extract.api:app --reload
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from src import config
from src.extract.extractor import extract
from src.governance import recorder
from src import pipeline

app = FastAPI(
    title="AP Invoice Processing API",
    description="Upload a PDF invoice for extraction and validation evidence.",
    version="0.2.0",
)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/health", tags=["meta"])
def health():
    api_key_set = config.get_api_key() is not None
    return {"status": "ok", "model": config.MODEL, "api_key_set": api_key_set}


@app.post("/extract", tags=["extraction"])
async def extract_invoice(file: UploadFile = File(...)):
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


@app.post("/process", tags=["pipeline"])
async def process_invoice_route(file: UploadFile = File(...)):
    tmp_path = await _save_upload(file)
    try:
        result = pipeline.process_invoice(str(tmp_path), invoice_path_label=file.filename)
    finally:
        tmp_path.unlink(missing_ok=True)
    return JSONResponse(content=result)


@app.get("/audit/{invoice_number:path}", tags=["governance"])
def audit(invoice_number: str):
    try:
        trail = recorder.fetch_audit_trail(invoice_number)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Audit store unavailable: {exc}")
    if not trail["runs"] and trail["latest_report"] is None:
        raise HTTPException(status_code=404, detail=f"No audit trail for {invoice_number}")
    return JSONResponse(content=trail)


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

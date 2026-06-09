"""FastAPI extraction service.

Routes
------
GET  /health     liveness check
POST /extract    upload a PDF → structured extraction JSON

Every successful extraction is logged to the run_log table.  Failed extractions
(API key missing, model error, bad PDF) still return a 200 with the structured
error payload so callers always get the same shape — the ``error`` field
distinguishes success from failure.

Start the server:
    uvicorn src.api:app --reload
"""
from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from src import config
from src.extract.extractor import extract

app = FastAPI(
    title="AP Invoice Extraction API",
    description="Upload a PDF invoice and receive structured extraction JSON.",
    version="0.1.0",
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
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Only PDF files are accepted.")

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)

    try:
        result = extract(str(tmp_path))
    finally:
        tmp_path.unlink(missing_ok=True)

    _log_run(file.filename, result)
    return JSONResponse(content=result)


# --------------------------------------------------------------------------- #
# Run-log persistence
# --------------------------------------------------------------------------- #
def _log_run(filename: str, result: dict) -> None:
    import json
    try:
        conn = sqlite3.connect(config.DB_PATH)
        conn.execute(
            """INSERT INTO run_log
               (created_at, invoice_path, source_type, extracted_json, overall_conf)
               VALUES (?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                filename,
                result.get("source_type"),
                json.dumps(result),
                (result.get("extraction_confidence") or {}).get("overall"),
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # logging failure must never break the response

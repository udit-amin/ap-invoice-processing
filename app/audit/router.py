"""Audit route: GET /audit/{invoice_number} — manager-only governance trail."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.auth.dependencies import CurrentUser, require_role
from app.governance import recorder

router = APIRouter(tags=["governance"])


@router.get("/audit/{invoice_number:path}")
def audit(
    invoice_number: str,
    user: CurrentUser = Depends(require_role("manager")),
):
    """Reconstruct the append-only audit trail for an invoice (managers only)."""
    try:
        trail = recorder.fetch_audit_trail(invoice_number)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Audit store unavailable: {exc}")
    if not trail["runs"] and trail["latest_report"] is None:
        raise HTTPException(status_code=404, detail=f"No audit trail for {invoice_number}")
    return JSONResponse(content=trail)

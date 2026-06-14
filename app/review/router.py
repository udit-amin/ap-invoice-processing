"""Review routes: the queue and the action endpoint. Either role may act."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.auth.dependencies import CurrentUser, get_current_user
from app.extract.vision_extract import render_page_png
from app.governance import recorder
from app.review import service

router = APIRouter(tags=["review"])


class ReviewActionRequest(BaseModel):
    action: str = Field(..., description="approve | reject | escalate")
    note: str | None = Field(default=None, description="Optional reviewer note")


@router.get("/review/queue")
def review_queue(user: CurrentUser = Depends(get_current_user)):
    """Flagged runs awaiting a human decision."""
    items = service.review_queue()
    return {"queue": items, "count": len(items)}


@router.post("/review/{run_id}/action")
def review_action(
    run_id: str,
    body: ReviewActionRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Apply approve/reject/escalate to a flagged run (records the actor).

    `approve` draws the matched PO down (race-safe); `reject`/`escalate` are
    record-only. 404 if the run has no verdict; 409 if an approve would
    over-commit the PO.
    """
    try:
        return service.apply_review_action(run_id, body.action, body.note, user)
    except service.ReviewError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


# Dynamic GETs registered after /review/queue so the literal "queue" path wins.
@router.get("/review/{run_id}")
def review_detail(run_id: str, user: CurrentUser = Depends(get_current_user)):
    """Full review context for a flagged run (either role — the queue is global).

    Powers the three flag-type detail views: drivers + review_payload, the
    extracted fields (with per-field confidence), and the per-line side-by-side.
    404 if the run has no verdict.
    """
    detail = service.review_detail(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"No verdict for run {run_id}")
    return detail


@router.get("/review/{run_id}/file")
def review_file(run_id: str, user: CurrentUser = Depends(get_current_user)):
    """Stream the original uploaded PDF for a flagged run (either role).

    Lets the low-confidence review view show the source scan. 404 if no file
    was stored for the run.
    """
    found = recorder.fetch_invoice_file(run_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"No source file for run {run_id}")
    filename, content_type, data = found
    return Response(
        content=data,
        media_type=content_type or "application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename or run_id}"'},
    )


@router.get("/review/{run_id}/preview")
def review_preview(
    run_id: str,
    page: int = 0,
    user: CurrentUser = Depends(get_current_user),
):
    """Render a page of the stored source PDF to PNG for an inline preview
    (more reliable than embedding the PDF). 404 if no file or the page is out
    of range / unrenderable.
    """
    found = recorder.fetch_invoice_file(run_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"No source file for run {run_id}")
    png = render_page_png(found[2], page=page)
    if png is None:
        raise HTTPException(status_code=404, detail="No such page or unrenderable PDF")
    return Response(content=png, media_type="image/png")

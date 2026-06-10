"""Review routes: the queue and the action endpoint. Either role may act."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.dependencies import CurrentUser, get_current_user
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

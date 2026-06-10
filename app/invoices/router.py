"""Invoice run views: list runs and fetch one run's detail.

Either role may call; the *scope* differs — a clerk sees only their own runs, a
manager sees all (enforced in the queries, not just the guard).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.dependencies import CurrentUser, get_current_user
from app.invoices import models

router = APIRouter(prefix="/invoices", tags=["invoices"])

_VERDICTS = {"APPROVE", "FLAG", "REJECT"}


@router.get("/runs")
def list_runs(
    verdict: str | None = Query(default=None, description="Filter by APPROVE|FLAG|REJECT"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: CurrentUser = Depends(get_current_user),
):
    """List processed runs (clerk → own, manager → all)."""
    if verdict is not None and verdict.upper() not in _VERDICTS:
        raise HTTPException(status_code=422, detail="verdict must be APPROVE, FLAG, or REJECT")
    runs = models.list_runs(
        role=user.role,
        actor_user_id=user.user_id,
        verdict=verdict.upper() if verdict else None,
        limit=limit,
        offset=offset,
    )
    return {"runs": runs, "count": len(runs), "limit": limit, "offset": offset}


@router.get("/runs/{run_id}")
def get_run(run_id: str, user: CurrentUser = Depends(get_current_user)):
    """One run's full detail. 404 if it doesn't exist or isn't the clerk's own."""
    run = models.get_run(run_id, role=user.role, actor_user_id=user.user_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"No run {run_id}")
    return run

"""Dashboard routes — manager-only (clerks get 403)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.auth.dependencies import CurrentUser, require_role
from app.dashboard import models

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary")
def summary(user: CurrentUser = Depends(require_role("manager"))):
    """Verdict mix, review backlog, and totals for the tenant."""
    return models.summary()


@router.get("/trends")
def trends(
    days: int = Query(default=30, ge=1, le=365),
    user: CurrentUser = Depends(require_role("manager")),
):
    """Per-day verdict counts over the last `days` days."""
    return models.trends(days)

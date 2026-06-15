"""Admin routes — demo affordances, manager-only and env-gated."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app import config
from app.admin import service
from app.auth.dependencies import CurrentUser, require_role

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/reset-demo")
def reset_demo(user: CurrentUser = Depends(require_role("manager"))):
    """Wipe operational data and re-seed the back-dated demo history (6/3/2).

    Destructive — gated behind ALLOW_DEMO_RESET so a real deployment can't be
    wiped by a click. Returns the verdict tally.
    """
    if not config.demo_reset_enabled():
        raise HTTPException(
            status_code=403,
            detail="Demo reset is disabled. Set ALLOW_DEMO_RESET=true to enable it.",
        )
    return service.reset_demo_data()

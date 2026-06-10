"""Policy routes — manager-only (clerks get 403)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.dependencies import CurrentUser, require_role
from app.policy import service

router = APIRouter(prefix="/policy", tags=["policy"])


class PolicyUpdate(BaseModel):
    auto_approve_ceiling: float | None = Field(default=None, gt=0)
    min_confidence: float | None = Field(default=None, ge=0, le=1)
    severity_overrides: dict[str, str] | None = Field(
        default=None, description="signal → APPROVE|FLAG|REJECT"
    )


@router.get("")
def get_policy(user: CurrentUser = Depends(require_role("manager"))):
    """Return the current governance policy."""
    try:
        return service.get_policy_row()
    except service.PolicyError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.put("")
def put_policy(
    body: PolicyUpdate,
    user: CurrentUser = Depends(require_role("manager")),
):
    """Edit the policy at runtime (validated, version-bumped, audited)."""
    try:
        return service.update_policy(body.model_dump(exclude_unset=True), user)
    except service.PolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

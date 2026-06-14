"""Governance policy for the decision engine.

The signal→severity map is *data-driven*: the default lives here, and any per-
signal remap in `policy_config.severity_overrides` (JSONB) wins. That is what
lets "a tolerance failure is a FLAG" become "…a REJECT" by editing data, no code
change (acceptance criterion #5).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.db.connection import cursor

# Verdict severities, ordered. Higher wins under precedence.
APPROVE = "APPROVE"
FLAG = "FLAG"
REJECT = "REJECT"
SEVERITY_RANK = {APPROVE: 0, FLAG: 1, REJECT: 2}

# Default signal → severity contributed *when that signal fires* (fails / trips).
# A signal that is clean contributes APPROVE (no escalation).
DEFAULT_SEVERITY = {
    "po_lookup":           REJECT,
    "vendor_approved":     REJECT,
    "po_status":           REJECT,
    "duplicate":           REJECT,
    "total_tolerance":     FLAG,
    "line_reconciliation": FLAG,
    "tax_present":         FLAG,
    "low_confidence":      FLAG,
    "incomplete":          FLAG,
    "over_authority":      FLAG,
}


@dataclass(frozen=True)
class Policy:
    auto_approve_ceiling: float
    min_confidence: float
    policy_version: str
    severity_overrides: dict = field(default_factory=dict)

    def severity_for(self, signal: str) -> str:
        """Severity a firing `signal` contributes, override beating default."""
        return self.severity_overrides.get(signal) or DEFAULT_SEVERITY.get(signal, FLAG)


def load_policy() -> Policy:
    """Read the single policy_config row into a Policy."""
    with cursor() as cur:
        cur.execute(
            """SELECT auto_approve_ceiling, min_confidence, policy_version,
                      severity_overrides
               FROM policy_config WHERE id = 1"""
        )
        row = cur.fetchone()
    if row is None:
        raise RuntimeError("policy_config is empty — run `python -m app.db.seed`")
    ceiling, min_conf, version, overrides = row
    return Policy(
        auto_approve_ceiling=float(ceiling),
        min_confidence=float(min_conf) if min_conf is not None else 0.0,
        policy_version=version or "unset",
        severity_overrides=dict(overrides or {}),
    )

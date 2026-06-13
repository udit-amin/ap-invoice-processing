"""Read + update the policy_config row, with validation and an audit event.

`update_policy` accepts a partial set of the editable fields, validates them,
bumps `policy_version`, and writes a `policy_change` governance event (run_id
NULL) recording who changed what — all in one transaction.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from psycopg.types.json import Jsonb

from app import config
from app.db.connection import cursor
from app.decide.policy import APPROVE, FLAG, REJECT, DEFAULT_SEVERITY
from app.governance import recorder

_SEVERITIES = {APPROVE, FLAG, REJECT}
_KNOWN_SIGNALS = set(DEFAULT_SEVERITY)
# Fields a manager may change at runtime.
_EDITABLE = ("auto_approve_ceiling", "min_confidence", "severity_overrides")


class PolicyError(Exception):
    """Validation failure (mapped to 400 by the router)."""


def get_policy_row() -> dict[str, Any]:
    """Return the full policy_config row as a dict."""
    with cursor() as cur:
        cur.execute(
            """SELECT auto_approve_ceiling, default_tolerance_pct, confidence_threshold,
                      min_confidence, policy_version, severity_overrides
               FROM policy_config WHERE id = 1"""
        )
        row = cur.fetchone()
    if row is None:
        raise PolicyError("policy_config is empty — run `python -m app.db.seed`")
    return {
        "auto_approve_ceiling": float(row[0]),
        "default_tolerance_pct": float(row[1]),
        "confidence_threshold": float(row[2]) if row[2] is not None else None,
        "min_confidence": float(row[3]) if row[3] is not None else None,
        "policy_version": row[4],
        "severity_overrides": dict(row[5] or {}),
    }


def _validate(changes: dict[str, Any]) -> dict[str, Any]:
    if not changes:
        raise PolicyError("no editable fields provided")
    clean: dict[str, Any] = {}
    for key, val in changes.items():
        if key not in _EDITABLE:
            raise PolicyError(f"field '{key}' is not editable (allowed: {list(_EDITABLE)})")
        if val is None:
            continue
        if key == "auto_approve_ceiling":
            if float(val) <= 0:
                raise PolicyError("auto_approve_ceiling must be > 0")
            clean[key] = float(val)
        elif key == "min_confidence":
            if not 0 <= float(val) <= 1:
                raise PolicyError("min_confidence must be between 0 and 1")
            clean[key] = float(val)
        elif key == "severity_overrides":
            if not isinstance(val, dict):
                raise PolicyError("severity_overrides must be an object")
            for sig, sev in val.items():
                if sig not in _KNOWN_SIGNALS:
                    raise PolicyError(f"unknown signal '{sig}' (known: {sorted(_KNOWN_SIGNALS)})")
                if sev not in _SEVERITIES:
                    raise PolicyError(f"severity '{sev}' must be one of {sorted(_SEVERITIES)}")
            clean[key] = val
    if not clean:
        raise PolicyError("no editable fields provided")
    return clean


def update_policy(changes: dict[str, Any], actor: object) -> dict[str, Any]:
    """Validate + apply policy changes, bump the version, and audit. Returns the
    new policy row."""
    clean = _validate(changes)
    before = get_policy_row()
    new_version = datetime.now(timezone.utc).strftime("%Y.%m.%d.%H%M%S")
    actor_label, actor_user_id, actor_role = recorder.actor_fields(actor)

    sets, vals = [], []
    for key, val in clean.items():
        sets.append(f"{key} = %s")
        vals.append(Jsonb(val) if key == "severity_overrides" else val)
    sets.append("policy_version = %s")
    vals.append(new_version)
    vals.append(config.TENANT_ID)

    with cursor(autocommit=False) as cur:
        cur.execute(
            f"UPDATE policy_config SET {', '.join(sets)} WHERE id = 1 AND tenant_id = %s",
            vals,
        )
        cur.execute(
            """INSERT INTO governance_events
               (run_id, stage, status, detail, actor, actor_user_id, actor_role, action_type)
               VALUES (NULL, 'policy', 'ok', %s, %s, %s, %s, 'policy_change')""",
            (Jsonb({"changed": clean, "from_version": before["policy_version"],
                    "to_version": new_version}),
             actor_label, actor_user_id, actor_role),
        )

    return get_policy_row()

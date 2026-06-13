"""The decision card — verdict + reason + drivers. The single most important
visual; used by the run view, review queue, and runs table.

Colour comes from the verdict only (Streamlit's semantic boxes). The reason is
shown verbatim (the engine already wrote it for humans); drivers are rendered
from the `drivers` array, never recomputed.
"""
from __future__ import annotations

import streamlit as st

import api_client

_BOX = {"APPROVE": st.success, "FLAG": st.warning, "REJECT": st.error}
_ICON = {"APPROVE": "✅", "FLAG": "⚠️", "REJECT": "⛔"}
_SEV_RANK = {"REJECT": 2, "FLAG": 1, "APPROVE": 0}


def _ordered(drivers: list[dict]) -> list[dict]:
    """Fired drivers first (most severe first), then the clean passes."""
    return sorted(drivers, key=lambda d: (
        0 if d.get("outcome") != "pass" else 1,
        -_SEV_RANK.get(d.get("severity"), 0),
        d.get("signal", ""),
    ))


def render(decision: dict) -> None:
    verdict = decision.get("verdict", "—")
    reason = decision.get("reason", "")
    box = _BOX.get(verdict, st.info)
    box(f"### {_ICON.get(verdict, '•')} {verdict}\n\n{reason}")

    drivers = decision.get("drivers") or []
    if drivers:
        st.markdown("**What mattered**")
        cols = st.columns(2)
        for i, d in enumerate(_ordered(drivers)):
            clean, fired = api_client.DRIVER_LABELS.get(
                d.get("signal", ""), (d.get("signal", ""), d.get("signal", "")))
            outcome = d.get("outcome")
            if outcome == "pass":
                line = f"✓ {clean}"
            elif outcome == "skip":
                line = f"➖ {clean} (skipped)"
            else:
                line = f"**✗ {fired}**"
            cols[i % 2].markdown(line)

    pol = decision.get("policy_version") or "—"
    conf = decision.get("confidence_overall")
    conf_txt = f"{conf:.0%}" if isinstance(conf, (int, float)) else "n/a"
    st.caption(f"Policy {pol} · extraction confidence {conf_txt}")

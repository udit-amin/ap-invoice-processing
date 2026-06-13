"""Policy editor (manager). Edit the auto-approve ceiling (and confidence gate);
the decision engine reads policy fresh, so a subsequent run reflects the change.
"""
from __future__ import annotations

import streamlit as st

import api_client
import fmt


def render() -> None:
    st.title("⚙️ Policy")
    try:
        p = api_client.get_policy()
    except api_client.ApiError as exc:
        st.error(exc.friendly())
        return

    st.caption(f"Current version: `{p.get('policy_version')}` · "
               f"tolerance {fmt.pct((p.get('default_tolerance_pct') or 0) / 100, 0)} (per-PO)")

    cur_ceiling = float(p.get("auto_approve_ceiling") or 0)
    cur_min_conf = float(p.get("min_confidence") or 0)

    with st.form("policy_form"):
        ceiling = st.number_input(
            "Auto-approve ceiling (₹)", min_value=1.0, value=cur_ceiling, step=10000.0,
            help="Invoices above this are flagged for human authority, even when all checks pass.",
        )
        min_conf = st.slider(
            "Minimum extraction confidence", 0.0, 1.0, cur_min_conf, 0.01,
            help="Below this, the invoice is flagged for a human to verify the read.",
        )
        submitted = st.form_submit_button("Save policy", type="primary")

    if not submitted:
        return

    changes: dict = {}
    if ceiling != cur_ceiling:
        changes["auto_approve_ceiling"] = ceiling
    if min_conf != cur_min_conf:
        changes["min_confidence"] = min_conf
    if not changes:
        st.info("No changes to save.")
        return

    try:
        new_p = api_client.put_policy(changes)
    except api_client.ApiError as exc:
        st.error(exc.friendly())
        return

    st.cache_data.clear()  # let the dashboard pick up downstream effects
    st.success(f"Policy updated → version `{new_p.get('policy_version')}`.")
    st.info("Process a **fresh** invoice in the Run view to see the change. Re-running an "
            "already-seen invoice returns REJECT (duplicate), which masks the ceiling effect.")

"""Processed invoices (clerk + manager) — every settled decision. A flagged
invoice only appears here once a human has reviewed it. Managers can change the
verdict (APPROVE ↔ REJECT) with a note; clerks are view-only. Never cached —
changes must show immediately.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import api_client
import fmt
import session
from components import decision_card, invoice_detail


def _effective(run: dict) -> str:
    """The verdict in force: a human override (approve/reject) wins over the AI's."""
    return (run.get("last_action") or "").upper() or (run.get("verdict") or "—")


def render() -> None:
    st.title("🗂️ Processed invoices")
    st.caption("Settled decisions — auto-approved/rejected, or flagged then resolved by a "
               "human. (A flagged invoice appears here once its review is complete.)")

    choice = st.selectbox("Filter by outcome", ["All", "APPROVE", "FLAG", "REJECT"])
    try:
        runs = api_client.get_runs(
            verdict=None if choice == "All" else choice, settled=True, limit=200).get("runs") or []
    except api_client.ApiError as exc:
        st.error(exc.friendly())
        return
    if not runs:
        st.info("Nothing settled yet — run a batch or work the review queue.")
        return

    st.dataframe(
        pd.DataFrame([{
            "Invoice": r.get("invoice_number"),
            "Vendor": r.get("vendor_name"),
            "Amount": r.get("invoice_total"),
            "AI verdict": r.get("verdict"),
            "Final": _effective(r),
            "Reviewed by": r.get("last_action_by") or "—",
            "When": fmt.short_ts(r.get("started_at")),
        } for r in runs]),
        hide_index=True, use_container_width=True,
        column_config={
            "Amount": st.column_config.NumberColumn(format="₹%.0f"),
        },
    )

    labels = {}
    for r in runs:
        tag = f" · {r['last_action']}ed by human" if r.get("last_action") else ""
        labels[f"{r.get('invoice_number')} — {_effective(r)}{tag}"] = r
    pick = st.selectbox("Open an invoice", ["—", *labels])
    if pick != "—":
        st.divider()
        _detail(labels[pick])


def _detail(run: dict) -> None:
    run_id = run["run_id"]
    try:
        d = api_client.get_review_detail(run_id)
    except api_client.ApiError as exc:
        st.error(exc.friendly())
        return

    st.subheader(f"{d.get('invoice_number')} · {d.get('vendor_name') or '—'}")
    decision_card.render(d)

    # If a human reviewed it, show who, when, and their note.
    la = d.get("latest_action")
    if la and la.get("action") in ("approve", "reject", "escalate"):
        who = la.get("actor_email") or la.get("actor_role") or "a reviewer"
        st.info(
            f"**{la['action'].capitalize()}ed** by **{who}** on "
            f"{fmt.short_ts(la.get('created_at'))}"
            + (f"\n\n**Note:** {la['note']}" if la.get("note") else "  \n_No note left._")
        )

    st.markdown("**Source document**")
    try:
        pdf = api_client.get_review_file(run_id)
    except api_client.ApiError:
        pdf = None
    invoice_detail.render_pdf(pdf, filename=f"{d.get('invoice_number', 'invoice')}.pdf",
                              key=f"proc_{run_id}")

    # Only managers can change a settled verdict; clerks are view-only.
    if session.role() != "manager":
        st.caption("Only a manager can change a settled verdict.")
        return

    effective = _effective(run)
    flip_to = "reject" if effective == "APPROVE" else "approve"
    st.markdown(f"**Change verdict** (currently **{effective}**)")
    note = st.text_area("Note (required)", key=f"ov_note_{run_id}",
                        placeholder=f"Why are you changing this to {flip_to.upper()}?")
    if st.button(f"Change to {flip_to.upper()}", key=f"ov_{run_id}", type="primary"):
        if not (note or "").strip():
            st.warning("A note is required to change the verdict.")
            return
        try:
            api_client.post_review_action(run_id, flip_to, note.strip())
        except api_client.ApiError as exc:
            st.error(exc.friendly())
            return
        st.toast(f"Invoice {d.get('invoice_number')} changed to {flip_to.upper()}.", icon="🔁")
        st.rerun()

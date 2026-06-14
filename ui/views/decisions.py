"""Processed invoices (clerk + manager) — monitor every AI decision and, when
needed, override it with a manual reject. Clerks see their own runs; managers
see all. Never cached — overrides must show immediately.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import api_client
import fmt
from components import decision_card, invoice_detail


def render() -> None:
    st.title("🗂️ Processed invoices")
    st.caption("Every invoice the pipeline has decided. Open one to review the decision "
               "or override it (manual reject).")

    choice = st.selectbox("Filter by verdict", ["All", "APPROVE", "FLAG", "REJECT"])
    try:
        runs = api_client.get_runs(
            verdict=None if choice == "All" else choice, limit=200).get("runs") or []
    except api_client.ApiError as exc:
        st.error(exc.friendly())
        return
    if not runs:
        st.info("No processed invoices yet.")
        return

    st.dataframe(
        pd.DataFrame([{
            "Invoice": r.get("invoice_number"),
            "Vendor": r.get("vendor_name"),
            "Amount": r.get("invoice_total"),
            "AI verdict": r.get("verdict"),
            "Override": (r.get("last_action") or "").upper() or "—",
            "Confidence": r.get("overall_conf"),
            "Who": r.get("actor_role") or "system",
            "When": fmt.short_ts(r.get("started_at")),
        } for r in runs]),
        hide_index=True, use_container_width=True,
        column_config={
            "Amount": st.column_config.NumberColumn(format="₹%.0f"),
            "Confidence": st.column_config.NumberColumn(format="%.2f"),
        },
    )

    labels = {}
    for r in runs:
        tag = f" · overridden: {r['last_action']}" if r.get("last_action") else ""
        labels[f"{r.get('invoice_number')} — {r.get('verdict')}{tag}"] = r
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

    st.markdown("**Source document**")
    try:
        pdf = api_client.get_review_file(run_id)
    except api_client.ApiError:
        pdf = None
    invoice_detail.render_pdf(pdf, filename=f"{d.get('invoice_number', 'invoice')}.pdf",
                              key=f"proc_{run_id}")

    last = run.get("last_action")
    if last:
        st.info(f"A human already **{last}ed** this invoice — overriding the AI verdict.")
        return
    if d.get("verdict") == "REJECT":
        st.caption("Already rejected by the pipeline — nothing to override.")
        return

    st.markdown("**Override**")
    note = st.text_area("Reason", key=f"ov_note_{run_id}",
                        placeholder="Why are you rejecting this auto-decision?")
    if st.button("⛔ Reject (override)", key=f"ov_rej_{run_id}", type="primary"):
        try:
            api_client.post_review_action(run_id, "reject", note or None)
        except api_client.ApiError as exc:
            st.error(exc.friendly())
            return
        st.toast(f"Invoice {d.get('invoice_number')} manually rejected.", icon="⛔")
        st.rerun()

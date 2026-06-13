"""Review queue (clerk + manager). Flagged items, worked oldest-first, with a
distinct 'what to check' view per flag type (§5.2). Never cached — it must
reflect actions immediately.
"""
from __future__ import annotations

import streamlit as st

import api_client
import fmt
from components import decision_card, invoice_detail

_SEL = "review_sel"


def render() -> None:
    st.title("📋 Review queue")
    try:
        data = api_client.get_review_queue()
    except api_client.ApiError as exc:
        st.error(exc.friendly())
        return

    # FIFO for fairness to vendors waiting on payment (presentation order).
    queue = sorted(data.get("queue") or [], key=lambda x: x.get("decided_at") or "")
    st.caption(f"{len(queue)} item(s) awaiting review — oldest first.")

    if not queue:
        st.success("Nothing in the queue. 🎉")
        return

    for item in queue:
        with st.container(border=True):
            left, right = st.columns([5, 1])
            with left:
                amt = item.get("invoice_total")
                st.markdown(f"**{item.get('invoice_number')}** · {item.get('vendor_name') or '—'}"
                            + (f" · {fmt.rupees(amt)}" if amt is not None else ""))
                st.caption(item.get("reason") or "")
                st.caption(f"Flagged {fmt.short_ts(item.get('decided_at'))}")
            with right:
                if st.button("Open", key=f"open_{item['run_id']}"):
                    st.session_state[_SEL] = item["run_id"]

    if st.session_state.get(_SEL):
        st.divider()
        _detail(st.session_state[_SEL])


def _detail(run_id: str) -> None:
    try:
        d = api_client.get_review_detail(run_id)
    except api_client.ApiError as exc:
        st.error(exc.friendly())
        return

    queue_kind = (d.get("review_payload") or {}).get("queue")
    st.subheader(f"Reviewing {d.get('invoice_number')}")
    st.info(api_client.QUEUE_LABELS.get(queue_kind, "Manual review required."))
    wtc = (d.get("review_payload") or {}).get("what_to_check")
    if wtc:
        st.markdown(f"**What to check:** {wtc}")

    # Each flag type gets the framing the human actually needs (§5.2).
    if queue_kind == "line_variance":
        st.markdown("**Invoice lines vs PO**")
        invoice_detail.line_table(d.get("line_reconciliation"))
    elif queue_kind in ("over_authority", "tolerance"):
        a, b = st.columns(2)
        a.metric("Invoice amount", fmt.rupees(d.get("invoice_total")))
        b.metric("Auto-approve ceiling", fmt.rupees(d.get("auto_approve_ceiling_applied")))
    elif queue_kind == "low_confidence":
        a, b = st.columns(2)
        with a:
            st.markdown("**Extracted fields**")
            invoice_detail.fields_panel(d.get("extraction"))
        with b:
            st.markdown("**Original scan**")
            try:
                pdf = api_client.get_review_file(run_id)
            except api_client.ApiError:
                pdf = None
            invoice_detail.render_scan(pdf, filename=f"{d.get('invoice_number', 'invoice')}.pdf")

    with st.expander("Full decision detail"):
        decision_card.render(d)

    st.markdown("**Action**")
    note = st.text_area("Note", key=f"note_{run_id}", placeholder="Optional note for the trail…")
    a, b, c = st.columns(3)
    if a.button("✅ Approve", key=f"appr_{run_id}", use_container_width=True):
        _act(run_id, "approve", note)
    if b.button("⛔ Reject", key=f"rej_{run_id}", use_container_width=True):
        _act(run_id, "reject", note)
    if c.button("⬆️ Escalate", key=f"esc_{run_id}", use_container_width=True):
        _act(run_id, "escalate", note)


def _act(run_id: str, action: str, note: str) -> None:
    try:
        res = api_client.post_review_action(run_id, action, note or None)
    except api_client.ApiError as exc:
        st.error(exc.friendly())
        return
    st.session_state[_SEL] = None
    verb = {"approve": "approved", "reject": "rejected", "escalate": "escalated"}[action]
    st.toast(f"Invoice {res.get('invoice_number')} {verb}.", icon="✅")
    st.rerun()

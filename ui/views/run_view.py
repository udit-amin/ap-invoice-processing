"""Run view (clerk) — the demo centrepiece. Upload an invoice, watch the real
stage events replay, then see the verdict and what the system saw.
"""
from __future__ import annotations

import time

import streamlit as st

import api_client
from components import decision_card, invoice_detail, stage_tracker

_STEP_DELAY = 0.32  # paced for the human watching; the backend runs in <1s


def _pending() -> list[dict[str, str]]:
    return [{"key": k, "label": label, "status": "pending"}
            for k, label in api_client.STAGE_SEQUENCE]


def _line_detail(validation: dict) -> list[dict] | None:
    for c in validation.get("checks") or []:
        if c.get("check") == "line_reconciliation":
            return c.get("detail")
    return None


def render() -> None:
    st.title("📤 Run an invoice")
    st.caption("Drop an invoice PDF and watch it go through the pipeline.")

    uploaded = st.file_uploader("Invoice PDF", type=["pdf"], label_visibility="collapsed")
    if uploaded is None:
        stage_tracker.render(_pending())
        return

    if st.button("Process invoice", type="primary"):
        _process(uploaded)


def _process(uploaded) -> None:
    data = uploaded.getvalue()
    placeholder = st.empty()
    stage_tracker.render(_pending(), into=placeholder)

    try:
        with st.spinner("Processing…"):
            result = api_client.process_invoice(uploaded.name, data)
    except api_client.ApiError as exc:
        st.error(exc.friendly())
        return

    # Replay the REAL events the backend emitted, paced for legibility (§4.2).
    final = api_client.stages_from_events(result.get("events") or [])
    view = _pending()
    for i, stage in enumerate(final):
        view[i] = stage
        stage_tracker.render(view, into=placeholder)
        time.sleep(_STEP_DELAY)
    stage_tracker.render(final, into=placeholder)
    st.caption("Stages above are the real governance events, paced for viewing — "
               "the backend processed this in well under a second.")

    st.divider()
    decision_card.render(result.get("decision") or {})

    extraction = result.get("extraction") or {}
    validation = result.get("validation") or {}
    with st.expander("What the system saw"):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Extracted fields**")
            invoice_detail.fields_panel(extraction)
        with c2:
            st.markdown("**Validation checks**")
            invoice_detail.checks_panel(validation.get("checks"))
        line_detail = _line_detail(validation)
        if line_detail:
            st.markdown("**Line items vs PO**")
            invoice_detail.line_table(line_detail)

    with st.expander("Developer view (raw response)"):
        st.json(result)

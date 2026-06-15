"""Batch ingest (clerk) — run several invoices through the pipeline in one go,
the same `/invoices/process` path as the run view, one invoice at a time.

Primary path is a multi-file **upload** (works against any deployment — the
browser sends the files). A server-side **folder** mode is kept for local runs and
parity with the landing→archive worker. No business logic here — per-file API
calls + a results table.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import pandas as pd
import streamlit as st

import api_client

_VERDICT_ICON = {"APPROVE": "✅", "FLAG": "⚠️", "REJECT": "⛔", "ERROR": "❗"}


def render() -> None:
    st.title("📥 Batch ingest")
    st.caption("Process several invoices in one run — the same pipeline as the run view, "
               "one at a time. (In production these arrive in an S3 landing bucket and are "
               "swept by the ingest worker; here you upload the batch.)")

    uploaded = st.file_uploader("Invoice PDFs", type=["pdf"], accept_multiple_files=True,
                                label_visibility="collapsed")
    if uploaded:
        st.write(f"Selected **{len(uploaded)}** file(s).")
        if st.button(f"Process {len(uploaded)} invoice(s)", type="primary"):
            _run([(f.name, f.getvalue()) for f in uploaded])

    with st.expander("Or process a server-side folder (local / worker parity)"):
        folder = st.text_input("Folder path (on the machine running this app)",
                               value="data/demo_live")
        path = Path(folder).expanduser()
        if not path.is_dir():
            st.caption(f"Not a folder: `{folder}`")
        else:
            pdfs = sorted(path.glob("*.pdf"))
            if not pdfs:
                st.caption("No PDF files found in that folder.")
            elif st.button(f"Process {len(pdfs)} file(s) from folder"):
                _run([(f.name, f.read_bytes()) for f in pdfs])


def _run(files: list[tuple[str, bytes]]) -> None:
    results = []
    bar = st.progress(0.0, text="Starting…")
    for i, (name, data) in enumerate(files, 1):
        bar.progress((i - 1) / len(files), text=f"Processing {name} ({i}/{len(files)})…")
        try:
            decision = (api_client.process_invoice(name, data).get("decision") or {})
            verdict = decision.get("verdict", "—")
            results.append({
                "File": name,
                "Invoice": decision.get("invoice_number") or "—",
                "Verdict": f"{_VERDICT_ICON.get(verdict, '')} {verdict}".strip(),
                "Reason": decision.get("reason") or "",
            })
        except api_client.ApiError as exc:
            results.append({"File": name, "Invoice": "—",
                            "Verdict": "❗ ERROR", "Reason": exc.friendly()})
    bar.empty()

    tally = Counter(r["Verdict"].split(" ")[-1] for r in results)
    st.success(f"Processed {len(results)} — " + " · ".join(f"{k}: {v}" for k, v in tally.items()))
    st.dataframe(pd.DataFrame(results), hide_index=True, use_container_width=True)
    st.caption("Already-seen invoices return REJECT (duplicate) by design — use the manager's "
               "**Reset demo data** button (Dashboard) for a clean run.")

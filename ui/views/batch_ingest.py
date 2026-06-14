"""Batch ingest (clerk) — run every PDF in a server-side folder through the
pipeline. Reads files from the folder where this app runs and calls the same
`/invoices/process` endpoint the run view uses, one invoice at a time. No
business logic here — folder I/O + per-file API calls + a results table.
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
    st.caption("Process every PDF in a folder through the pipeline — the same path "
               "as the run view, one invoice at a time.")

    folder = st.text_input("Folder path (on the machine running this app)",
                           value="data/inputs")
    path = Path(folder).expanduser()
    if not path.is_dir():
        st.warning(f"Not a folder: `{folder}`")
        return

    pdfs = sorted(path.glob("*.pdf"))
    if not pdfs:
        st.info("No PDF files found in that folder.")
        return

    st.write(f"Found **{len(pdfs)}** PDF(s).")
    with st.expander("Files"):
        for f in pdfs:
            st.caption(f.name)

    if st.button(f"Process {len(pdfs)} invoice(s)", type="primary"):
        _run(pdfs)


def _run(pdfs: list[Path]) -> None:
    results = []
    bar = st.progress(0.0, text="Starting…")
    for i, f in enumerate(pdfs, 1):
        bar.progress((i - 1) / len(pdfs), text=f"Processing {f.name} ({i}/{len(pdfs)})…")
        try:
            decision = (api_client.process_invoice(f.name, f.read_bytes()).get("decision") or {})
            verdict = decision.get("verdict", "—")
            results.append({
                "File": f.name,
                "Invoice": decision.get("invoice_number") or "—",
                "Verdict": f"{_VERDICT_ICON.get(verdict, '')} {verdict}".strip(),
                "Reason": decision.get("reason") or "",
            })
        except api_client.ApiError as exc:
            results.append({"File": f.name, "Invoice": "—",
                            "Verdict": "❗ ERROR", "Reason": exc.friendly()})
    bar.empty()

    tally = Counter(r["Verdict"].split(" ")[-1] for r in results)
    st.success(f"Processed {len(results)} — " + " · ".join(f"{k}: {v}" for k, v in tally.items()))
    st.dataframe(pd.DataFrame(results), hide_index=True, use_container_width=True)
    st.caption("Already-seen invoices return REJECT (duplicate) by design — truncate the "
               "operational tables and reseed for a clean run.")

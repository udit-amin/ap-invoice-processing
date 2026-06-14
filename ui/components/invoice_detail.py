"""'What the system saw' building blocks: extracted fields (with low-confidence
marks), the six-check evidence, the per-line invoice-vs-PO table, and the
original scan. Pure rendering — all data comes from the API.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import api_client

_CHECK_ICON = {"pass": "✅", "fail": "❌", "skip": "➖"}


def fields_panel(extraction: dict | None, conf_threshold: float = 0.80) -> None:
    """Extracted header fields; fields whose per-field confidence is below the
    threshold are flagged (the low-confidence review framing)."""
    if not extraction:
        st.caption("No extracted fields stored for this run.")
        return
    conf = extraction.get("extraction_confidence") or {}
    rows = [
        ("Invoice #",     extraction.get("invoice_number"), conf.get("invoice_number")),
        ("Vendor",        extraction.get("vendor_name"),    conf.get("vendor_name")),
        ("PO reference",  extraction.get("po_reference"),   conf.get("po_reference")),
        ("Invoice date",  extraction.get("invoice_date"),   None),
        ("Currency",      extraction.get("currency"),       None),
        ("Subtotal",      extraction.get("subtotal"),       None),
        ("Total",         extraction.get("total"),          conf.get("total")),
    ]
    for label, value, c in rows:
        flag = ""
        if isinstance(c, (int, float)) and c < conf_threshold:
            flag = f" &nbsp;:red[⚠ low confidence ({c:.0%})]"
        shown = value if value not in (None, "") else "—"
        st.markdown(f"**{label}:** {shown}{flag}", unsafe_allow_html=True)


def checks_panel(checks: list[dict] | None) -> None:
    """The six validation checks with pass/fail/skip + the plain-English reason."""
    if not checks:
        st.caption("No validation evidence stored.")
        return
    for c in checks:
        label = api_client.CHECK_LABELS.get(c.get("check"), c.get("check"))
        icon = _CHECK_ICON.get(c.get("status"), "•")
        st.markdown(f"{icon} **{label}** — {c.get('reason', '')}")


def line_table(line_detail: list[dict] | None) -> None:
    """Invoice lines vs PO lines, side by side, mismatches marked (line-variance)."""
    if not line_detail:
        st.caption("No line-level detail for this invoice.")
        return
    rows = []
    for d in line_detail:
        inv = d.get("invoice") or {}
        po = d.get("po") or {}
        ok = d.get("classification") == "exact_match"
        rows.append({
            " ": "✓" if ok else "⚠",
            "Item (invoice)": d.get("invoice_line") or "—",
            "Matched PO line": d.get("matched_po_line") or "—",
            "Inv qty": inv.get("qty"),
            "PO qty": po.get("qty"),
            "Inv price": inv.get("unit_price"),
            "PO price": po.get("unit_price"),
            "Finding": (d.get("classification") or "").replace("_", " "),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def render_pdf(pdf_bytes: bytes | None, filename: str = "invoice.pdf",
               height: int = 520, key: str | None = None) -> None:
    """Inline preview of the original PDF — a native render, so a text invoice
    shows selectable text and a scanned one shows its page image — with a
    download. Used for every invoice, not just scans."""
    if not pdf_bytes:
        st.caption("No source document stored for this run.")
        return
    st.pdf(pdf_bytes, height=height, key=key)
    st.download_button("⬇ Download original", data=pdf_bytes, file_name=filename,
                       mime="application/pdf", key=f"dl_{key}" if key else None)

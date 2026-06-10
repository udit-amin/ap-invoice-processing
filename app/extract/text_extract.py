"""Text path — pull the raw text layer out of a machine-readable PDF."""
from __future__ import annotations

import pdfplumber


def get_text(path) -> str:
    """Return the concatenated text of all pages, page-delimited."""
    parts = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            parts.append(f"--- page {i} ---\n{text}")
    return "\n\n".join(parts).strip()

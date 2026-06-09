"""PDF type detection — routes a PDF to the text or vision extraction path.

Tries pdfplumber; if the text layer is below a low per-page character threshold
(config.SCANNED_CHAR_THRESHOLD) the PDF is treated as image-only and routed to
the vision path. This is the single place the text/scanned decision is made.
"""
from __future__ import annotations

import pdfplumber

from src import config


def page_text_chars(path) -> tuple[int, int]:
    """Return (total_text_chars, page_count) using pdfplumber."""
    with pdfplumber.open(path) as pdf:
        pages = pdf.pages
        total = sum(len(page.extract_text() or "") for page in pages)
        return total, len(pages)


def detect_type(path) -> str:
    """Return "text" or "scanned".

    Scanned when the extracted text is empty or below
    ``SCANNED_CHAR_THRESHOLD`` characters per page.
    """
    total_chars, page_count = page_text_chars(path)
    page_count = max(page_count, 1)
    if total_chars < config.SCANNED_CHAR_THRESHOLD * page_count:
        return "scanned"
    return "text"

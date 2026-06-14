"""Vision path — rasterise a PDF to base64 page images for Claude.

Uses PyMuPDF (no poppler dependency). Each page becomes a base64-encoded PNG
ready for an Anthropic image content block.
"""
from __future__ import annotations

import base64

import fitz  # PyMuPDF

from app import config


def get_images(path) -> list[dict]:
    """Return a list of ``{"media_type", "data"}`` dicts, one per page.

    ``data`` is base64-encoded PNG bytes rendered at ``config.RASTER_DPI``.
    """
    zoom = config.RASTER_DPI / 72.0
    mat = fitz.Matrix(zoom, zoom)
    images: list[dict] = []
    with fitz.open(path) as doc:
        for page in doc:
            pix = page.get_pixmap(matrix=mat)
            png_bytes = pix.tobytes("png")
            images.append({
                "media_type": "image/png",
                "data": base64.standard_b64encode(png_bytes).decode("ascii"),
            })
    return images


def render_page_png(pdf_bytes: bytes, page: int = 0, dpi: int = 144) -> bytes | None:
    """Render one page of an in-memory PDF to PNG bytes — used by the review UI to
    preview the stored source document. Returns None if the bytes aren't a valid
    PDF or the page is out of range."""
    try:
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            if page < 0 or page >= doc.page_count:
                return None
            return doc[page].get_pixmap(matrix=mat).tobytes("png")
    except Exception:  # noqa: BLE001 — a bad/again-unreadable PDF just has no preview
        return None

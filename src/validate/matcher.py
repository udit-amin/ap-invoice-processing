"""Fuzzy matching utilities for vendor names and line-item descriptions.

Design decisions:
- Vendor floor 0.75: vendor names carry many legal suffixes ("India Pvt Ltd",
  "Group") that add noise; a lower floor avoids false negatives.
- Line floor 0.80: descriptions are more specific so a higher floor keeps
  false-positive matches out.
- rapidfuzz.fuzz.token_sort_ratio: sorts tokens before comparing, so
  "Senior consultant (hours)" and "(hours) Senior consultant" score 100.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

_VENDOR_FLOOR = 75   # token_sort_ratio is 0-100
_LINE_FLOOR   = 80

_LEGAL_SUFFIXES = re.compile(
    r"\b(pvt\s*ltd|private\s+limited|india|inc|llc|corp|limited|group"
    r"|co\b|ltd|plc|ag|gmbh|sdn\s+bhd|pty\s+ltd)\b",
    re.IGNORECASE,
)
_PUNCT = re.compile(r"[^\w\s]")
_WS    = re.compile(r"\s+")

_ABBREVS = {
    "hrs": "hours",
    "qty": "quantity",
    r"no\.": "number",
    "mgr": "manager",
    "sr":  "senior",
    "jr":  "junior",
    "dept": "department",
    "mfg": "manufacturing",
    "maint": "maintenance",
}


def normalize_vendor(name: str) -> str:
    s = name.lower()
    s = _LEGAL_SUFFIXES.sub(" ", s)
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


def normalize_line(desc: str) -> str:
    s = desc.lower()
    for abbr, full in _ABBREVS.items():
        s = re.sub(r"\b" + abbr + r"\b", full, s)
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


def _vendor_score(a: str, b: str) -> float:
    return fuzz.token_sort_ratio(normalize_vendor(a), normalize_vendor(b))


def _line_score(a: str, b: str) -> float:
    return fuzz.token_sort_ratio(normalize_line(a), normalize_line(b))


def find_vendor(invoice_name: str | None, registry: list[dict]) -> dict | None:
    """Return the registry entry with the highest token_sort_ratio, or None."""
    if not invoice_name:
        return None
    best_entry, best_score = None, 0
    for entry in registry:
        score = _vendor_score(invoice_name, entry["vendor_name"])
        if score > best_score:
            best_score, best_entry = score, entry
    if best_score >= _VENDOR_FLOOR:
        return best_entry
    return None


def line_similarity(a: str, b: str) -> float:
    """Return 0.0–1.0 similarity between two line descriptions."""
    return _line_score(a, b) / 100.0


def match_lines(
    invoice_lines: list[dict],
    po_lines: list[dict],
    tolerance_pct: float,
    tax_rate: float | None,
    embedded_tax: bool,
) -> list[dict]:
    """Match invoice lines to PO lines and classify each pair.

    Returns a list of detail dicts.  Unmatched invoice lines and uninvoiced PO
    lines are appended at the end.

    Line-level price tolerance reuses the PO's tolerance_pct.
    """
    tol = tolerance_pct / 100.0
    rate_factor = (1 + tax_rate / 100.0) if (embedded_tax and tax_rate) else None

    remaining_po = list(range(len(po_lines)))
    detail: list[dict] = []

    for inv_line in invoice_lines:
        if inv_line.get("is_bundle"):
            continue
        inv_desc = inv_line.get("description", "")
        inv_qty  = inv_line.get("quantity")
        inv_up   = inv_line.get("unit_price")
        if inv_up is not None and rate_factor is not None:
            inv_up = inv_up / rate_factor

        best_po_idx, best_score = None, 0.0
        for po_idx in remaining_po:
            score = _line_score(inv_desc, po_lines[po_idx]["description"]) / 100.0
            if score > best_score:
                best_score, best_po_idx = score, po_idx

        if best_po_idx is None or best_score < _LINE_FLOOR / 100.0:
            detail.append({
                "invoice_line":     inv_desc,
                "matched_po_line":  None,
                "classification":   "unmatched_invoice_line",
                "invoice": {"qty": inv_qty, "unit_price": inv_up},
                "po":      None,
            })
            continue

        remaining_po.remove(best_po_idx)
        po_line = po_lines[best_po_idx]
        po_qty  = po_line.get("quantity")
        po_up   = po_line.get("unit_price")

        qty_match   = (inv_qty == po_qty)
        price_match = (
            inv_up is None
            or po_up is None
            or abs(inv_up - po_up) / max(po_up, 1) <= tol
        )

        if qty_match and price_match:
            cls = "exact_match"
        elif qty_match:
            cls = "price_variance"
        elif price_match:
            cls = "qty_variance"
        else:
            cls = "qty_and_price_variance"

        detail.append({
            "invoice_line":    inv_desc,
            "matched_po_line": po_line["description"],
            "classification":  cls,
            "invoice": {"qty": inv_qty, "unit_price": round(inv_up, 2) if inv_up is not None else None},
            "po":      {"qty": po_qty,  "unit_price": po_up},
        })

    for po_idx in remaining_po:
        po_line = po_lines[po_idx]
        detail.append({
            "invoice_line":    None,
            "matched_po_line": po_line["description"],
            "classification":  "uninvoiced_po_line",
            "invoice":         None,
            "po": {"qty": po_line.get("quantity"), "unit_price": po_line.get("unit_price")},
        })

    return detail

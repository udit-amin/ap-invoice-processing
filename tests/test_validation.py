"""Pure validation-logic tests — no database, no API key required.

These exercise the deterministic core: fuzzy matching, line classification, and
the five stateless check functions.  The full validate() orchestration and the
DB-backed duplicate check are covered in test_governance.py (skip-if-down).
"""
from __future__ import annotations

import pytest

from app.validate import checks, matcher

# ---------------------------------------------------------------------------
# Inline reference data (decoupled from Postgres so these tests need no infra)
# ---------------------------------------------------------------------------

PO_5001 = {
    "po_id": "PO-5001", "vendor_name": "Dell Technologies India Pvt Ltd",
    "remaining_balance": 566400.0, "status": "open", "tolerance_pct": 3.0,
    "expected_line_items": [
        {"description": "Latitude 5440 Laptop", "quantity": 5, "unit_price": 72000.0},
        {"description": 'UltraSharp 24" Monitor', "quantity": 5, "unit_price": 24000.0},
    ],
}
PO_5007 = {
    "po_id": "PO-5007", "vendor_name": "Apex Consulting Group",
    "remaining_balance": 460200.0, "status": "open", "tolerance_pct": 5.0,
    "expected_line_items": [
        {"description": "Senior consultant (hrs)", "quantity": 60, "unit_price": 4500.0},
        {"description": "Project manager (hrs)", "quantity": 20, "unit_price": 6000.0},
    ],
}
PO_5009 = {
    "po_id": "PO-5009", "vendor_name": "TechGear Distributors",
    "remaining_balance": 802400.0, "status": "open", "tolerance_pct": 5.0,
    "expected_line_items": [
        {"description": "Laptop (TechGear TG-500)", "quantity": 10, "unit_price": 65000.0},
        {"description": "Wireless Headphones", "quantity": 10, "unit_price": 3000.0},
    ],
}
PO_5010 = {
    "po_id": "PO-5010", "vendor_name": "BluePrint Marketing",
    "remaining_balance": 354000.0, "status": "open", "tolerance_pct": 5.0,
    "expected_line_items": [
        {"description": "Social media campaign (Q2)", "quantity": 1, "unit_price": 200000.0},
        {"description": "Creative design retainer", "quantity": 1, "unit_price": 100000.0},
    ],
}
PO_CLOSED = {
    "po_id": "PO-5003", "vendor_name": "Quanta Networks",
    "remaining_balance": 0.0, "status": "closed", "tolerance_pct": 5.0,
    "expected_line_items": [],
}

REGISTRY = [
    {"vendor_id": "V-001", "vendor_name": "Dell Technologies India Pvt Ltd",
     "approved": True, "category": "Hardware"},
    {"vendor_id": "V-004", "vendor_name": "Apex Consulting Group",
     "approved": True, "category": "Consulting"},
    {"vendor_id": "V-010", "vendor_name": "Globex Corporation",
     "approved": False, "category": "Unknown"},
]


def _line(desc, qty, unit_price, is_bundle=False, bundle_components=None):
    return {
        "description": desc, "quantity": qty, "unit_price": unit_price,
        "line_total": (qty or 0) * (unit_price or 0),
        "is_bundle": is_bundle, "bundle_components": bundle_components or [],
    }


def _extracted(line_items, tax_treatment="separated", tax_rate=18):
    return {
        "line_items": line_items,
        "tax": {"amount": None, "rate_pct": tax_rate, "treatment": tax_treatment},
    }


# ---------------------------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------------------------

def test_line_similarity_hours_vs_hrs():
    score = matcher.line_similarity("Senior consultant (hours)", "Senior consultant (hrs)")
    assert score >= 0.80, f"expected ≥0.80, got {score:.3f}"


def test_find_vendor_suffix_variation():
    assert matcher.find_vendor("Dell Technologies", REGISTRY)["vendor_id"] == "V-001"


def test_find_vendor_full_name():
    assert matcher.find_vendor("Dell Technologies India Pvt Ltd", REGISTRY)["vendor_id"] == "V-001"


def test_find_vendor_unrecognised_returns_none():
    assert matcher.find_vendor("Totally Unknown Corp XYZ", REGISTRY) is None


# ---------------------------------------------------------------------------
# PO lookup
# ---------------------------------------------------------------------------

def test_po_lookup_found():
    result, po = checks.check_po_lookup("PO-5001", {"PO-5001": PO_5001})
    assert result["status"] == "pass" and po["po_id"] == "PO-5001"


def test_po_lookup_missing():
    result, po = checks.check_po_lookup("PO-9999", {"PO-5001": PO_5001})
    assert result["status"] == "fail" and po is None


# ---------------------------------------------------------------------------
# Vendor approval
# ---------------------------------------------------------------------------

def test_vendor_approved():
    assert checks.check_vendor_approval("Dell Technologies", REGISTRY)["status"] == "pass"


def test_vendor_unapproved_fails():
    assert checks.check_vendor_approval("Globex Corporation", REGISTRY)["status"] == "fail"


# ---------------------------------------------------------------------------
# PO status
# ---------------------------------------------------------------------------

def test_po_status_open():
    assert checks.check_po_status(PO_5001)["status"] == "pass"


def test_po_status_closed():
    r = checks.check_po_status(PO_CLOSED)
    assert r["status"] == "fail" and "closed" in r["reason"].lower()


# ---------------------------------------------------------------------------
# Total tolerance
# ---------------------------------------------------------------------------

def test_total_tolerance_exact_match():
    # edge_4: total equals PO balance exactly → pass even though lines differ
    assert checks.check_total_tolerance(566400, PO_5001)["status"] == "pass"


def test_total_tolerance_breach():
    r = checks.check_total_tolerance(700000, PO_5001)
    assert r["status"] == "fail"


# ---------------------------------------------------------------------------
# Line reconciliation
# ---------------------------------------------------------------------------

def test_line_recon_exact_pass():
    extracted = _extracted([_line("Latitude 5440 Laptop", 5, 72000),
                            _line('UltraSharp 24" Monitor', 5, 24000)])
    assert checks.check_line_reconciliation(extracted, PO_5001)["status"] == "pass"


def test_line_recon_edge4_qty_and_price_variance():
    # edge_4: 7@60000 + 3@20000 vs ordered 5@72000 + 5@24000
    extracted = _extracted([_line("Latitude 5440 Laptop", 7, 60000),
                            _line('UltraSharp 24" Monitor', 3, 20000)])
    r = checks.check_line_reconciliation(extracted, PO_5001)
    assert r["status"] == "fail"
    assert len(r["detail"]) == 2
    assert {d["classification"] for d in r["detail"]} == {"qty_and_price_variance"}


def test_line_recon_fuzzy_hours_pass():
    # normal_4: invoice says "(hours)"; PO says "(hrs)"
    extracted = _extracted([_line("Senior consultant (hours)", 60, 4500),
                            _line("Project manager (hours)", 20, 6000)])
    assert checks.check_line_reconciliation(extracted, PO_5007)["status"] == "pass"


def test_line_recon_bundled_skips():
    extracted = _extracted([_line("Productivity Bundle (Laptop + Headphones)", 10, 68000,
                                  is_bundle=True, bundle_components=["Laptop", "Headphones"])])
    r = checks.check_line_reconciliation(extracted, PO_5009)
    assert r["status"] == "skip" and "bundled" in r["reason"].lower()


def test_line_recon_embedded_tax_derives_ex_tax_and_passes():
    # 236000 / 1.18 = 200000 ; 118000 / 1.18 = 100000 → matches PO ex-tax lines
    extracted = _extracted(
        [_line("Social media campaign (Q2)", 1, 236000),
         _line("Creative design retainer", 1, 118000)],
        tax_treatment="embedded", tax_rate=18,
    )
    assert checks.check_line_reconciliation(extracted, PO_5010)["status"] == "pass"


def test_line_recon_embedded_tax_unknown_rate_skips():
    extracted = _extracted(
        [_line("Social media campaign (Q2)", 1, 236000)],
        tax_treatment="embedded", tax_rate=None,
    )
    r = checks.check_line_reconciliation(extracted, PO_5010)
    assert r["status"] == "skip"


# ---- check 7: tax presence (pure; no PO, no amounts) ----

def test_tax_present_separated_passes():
    assert checks.check_tax_present(_extracted([], tax_treatment="separated"))["status"] == "pass"


def test_tax_present_embedded_passes():
    assert checks.check_tax_present(_extracted([], tax_treatment="embedded"))["status"] == "pass"


def test_tax_present_none_fails():
    r = checks.check_tax_present(_extracted([], tax_treatment="none"))
    assert r["status"] == "fail" and "tax" in r["reason"].lower()


def test_tax_present_unknown_skips():
    # The answer-key / dry-run shape carries treatment=null → skip, not fail, so
    # the verdict matrix is unchanged when tax isn't modelled.
    assert checks.check_tax_present(_extracted([], tax_treatment=None))["status"] == "skip"
    assert checks.check_tax_present({})["status"] == "skip"

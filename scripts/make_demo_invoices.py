"""Generate the demo invoice sets, fresh-numbered so they're repeatable after a
reset (no duplicate clashes) and mapped to the seeded POs.

  data/demo/batch/  — a straight-through batch (no human review):
        3 APPROVE (Stellar/PO-5005, Nimbus/PO-5008, Apex/PO-5007) + 2 REJECT
        (Globex — unapproved vendor; Quanta — closed PO-5003).
  data/demo/edges/  — the edge cases, uploaded one at a time:
        over-ceiling   (TechGear/PO-5009, ₹8.02L > ₹7.5L)        → FLAG
        missing-tax    (FastFreight/PO-5011, no GST)             → FLAG
        line-variance  (Dell/PO-5001, total matches but lines don't) → FLAG
        scanned image  (GreenLeaf/PO-5006, image-only)           → APPROVE (vision path)
        Duplicate detection: re-drag any batch file → REJECT.

The demo starts on a clean slate (the reset seeds no runs); the batch and the edge
uploads build the whole picture up live.

    .venv/bin/python scripts/make_demo_invoices.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config                                              # noqa: E402
from app.generate import invoice_generator as gen                   # noqa: E402
from app.generate.invoice_generator import (                        # noqa: E402
    InvoiceSpec, LineItem, _render_pdf_bytes, _rasterise_to_image_pdf, _separated,
)

DEMO_DIR = config.DATA_DIR / "demo"

# GSTIN/terms for the demo vendors so headers render fully (cosmetic).
_DEMO_VENDOR_META = {
    "Stellar Software Solutions": ("27BBBBB0001B1Z5", "Net-30"),
    "Nimbus Networks":            ("36EEEEE0004E1Z2", "Net-30"),
    "Apex Consulting Group":      ("29DDDDD0003D1Z3", "Net-30"),
    "FastFreight Logistics":      ("24CCCCC0002C1Z4", "Net-45"),
    "TechGear Distributors":      ("29HHHHH0007H1Z1", "Net-30"),
    "Quanta Networks":            ("29AAAAA0009A1Z7", "Net-30"),
    "Globex Corporation":         ("07GGGGG0006G1Z0", "Net-30"),
    "GreenLeaf Facilities Management": ("29AAAAA0010A1Z6", "Net-15"),
    "Dell Technologies India Pvt Ltd": ("29AAAAA0001A1Z5", "Net-30"),
}


def _items(*triples) -> list[LineItem]:
    return [LineItem(d, q, u, q * u) for (d, q, u) in triples]


def _sep(filename, vendor, number, po, items) -> InvoiceSpec:
    """A normal separated-GST invoice whose total = subtotal + 18% tax."""
    sub = sum(i.line_total for i in items)
    tax, tot = _separated(sub)
    return InvoiceSpec(filename, vendor, number, "2026-06-14", po, items,
                       "separated", 18, sub, tax, tot)


def batch_specs() -> list[InvoiceSpec]:
    return [
        # --- APPROVE: total = PO within tolerance, under the ₹7.5L ceiling ---
        _sep("batch_approve_stellar.pdf", "Stellar Software Solutions", "SSS-2026-0461",
             "PO-5005", _items(("Analytics Suite annual license", 20, 9500),
                               ("Onboarding & setup", 1, 35000))),                 # 2,65,500
        _sep("batch_approve_nimbus.pdf", "Nimbus Networks", "NN-2026-0608",
             "PO-5008", _items(("Leased line 100 Mbps", 1, 85000),
                               ("Static IP block", 1, 5000))),                     # 1,06,200
        _sep("batch_approve_apex.pdf", "Apex Consulting Group", "APX-2026-0212",
             "PO-5007", _items(("Senior consultant (hours)", 60, 4500),
                               ("Project manager (hours)", 20, 6000))),            # 4,60,200
        # --- REJECT: unapproved vendor / closed PO (straight-through, no review) ---
        _sep("batch_reject_globex.pdf", "Globex Corporation", "GLX-2026-0099",
             "PO-5002", _items(("Consulting services — June", 1, 80000))),
        _sep("batch_reject_quanta.pdf", "Quanta Networks", "QN-2026-0303",
             "PO-5003", _items(("Networking equipment", 1, 200000))),  # PO-5003 is closed
    ]


def edge_specs() -> list[InvoiceSpec]:
    # over-ceiling: TechGear / PO-5009 → ₹8,02,400 > ₹7,50,000 ceiling → FLAG.
    over = _sep("edge_over_ceiling_techgear.pdf", "TechGear Distributors", "TG-2026-0511",
                "PO-5009", _items(("Laptop (TechGear TG-500)", 10, 65000),
                                  ("Wireless Headphones", 10, 3000)))
    # missing-tax: FastFreight / PO-5011 (stored ex-tax) → only tax_present fails → FLAG.
    nt = _items(("Inter-state freight (zero-rated)", 1, 200000))
    notax = InvoiceSpec("edge_missing_tax_fastfreight.pdf", "FastFreight Logistics",
                        "FF-2026-0614", "2026-06-14", "PO-5011", nt, "none", None,
                        subtotal=200000.0, tax_amount=None, total=200000.0)
    # line-variance: Dell / PO-5001 — total matches the PO within tolerance (₹5,66,400)
    # but the line quantities don't reconcile (4/8 vs the PO's 5/5) → FLAG.
    lv = _sep("edge_line_variance_dell.pdf", "Dell Technologies India Pvt Ltd",
              "DELL-2026-0614", "PO-5001",
              _items(("Latitude 5440 Laptop", 4, 72000),
                     ('UltraSharp 24" Monitor', 8, 24000)))
    # scanned image: GreenLeaf / PO-5006, image-only so the vision path runs. Reads
    # cleanly and matches the PO → APPROVE (shows the system handles scans).
    sc = _items(("Monthly Housekeeping - May", 1, 65000),
                ("Deep Cleaning (one-time)", 1, 18000))
    sub = sum(i.line_total for i in sc)
    tax, tot = _separated(sub)
    scan = InvoiceSpec("edge_scanned_greenleaf.pdf", "GreenLeaf Facilities Management",
                       "GLF-2026-0440", "2026-06-13", "PO-5006", sc, "separated", 18,
                       sub, tax, tot, scanned=True)
    return [over, notax, lv, scan]


def main() -> None:
    gen._VENDOR_META.update(_DEMO_VENDOR_META)
    for sub, specs in (("batch", batch_specs()), ("edges", edge_specs())):
        out = DEMO_DIR / sub
        out.mkdir(parents=True, exist_ok=True)
        for stale in out.glob("*.pdf"):   # idempotent: drop any prior set
            stale.unlink()
        for spec in specs:
            pdf = _render_pdf_bytes(spec)
            if spec.scanned:  # image-only so the vision path runs (reads cleanly)
                pdf = _rasterise_to_image_pdf(pdf)
            (out / spec.filename).write_bytes(pdf)
            print(f"  demo/{sub}/{spec.filename:34s} {spec.invoice_number:14s} {spec.vendor_name}")
    print("\nBatch = straight-through (3 APPROVE + 2 REJECT); edges = the cases to walk "
          "through one at a time (3 FLAG + 1 scanned APPROVE; duplicate = re-drag a batch file).")


if __name__ == "__main__":
    main()

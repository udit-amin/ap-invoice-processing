"""Generate the demo invoice sets, fresh-numbered so they're repeatable after a
reset (no duplicate clashes) and mapped to the seeded POs.

  data/demo/batch/  — a straight-through batch (no human review):
        3 APPROVE (Stellar/PO-5005, Nimbus/PO-5008, Apex/PO-5007) + 2 REJECT
        (Globex — unapproved vendor; Quanta — closed PO-5003).
  data/demo/edges/  — flag-producing invoices to upload one at a time:
        over-ceiling (TechGear/PO-5009, ₹8.02L > ₹7.5L) and missing-tax
        (FastFreight/PO-5011, no GST). Duplicate detection: re-drag any batch file.

The two *starting* flags (line-variance + low-confidence scan) are seeded
separately by the reset (app/admin/service.py), so the demo opens with a non-empty
queue and the batch + edges build the rest up live.

    .venv/bin/python scripts/make_demo_invoices.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config                                              # noqa: E402
from app.generate import invoice_generator as gen                   # noqa: E402
from app.generate.invoice_generator import (                        # noqa: E402
    InvoiceSpec, LineItem, _render_pdf_bytes, _separated,
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
    # over-ceiling: TechGear / PO-5009 → ₹8,02,400 > ₹7,50,000 ceiling.
    over = _sep("edge_over_ceiling_techgear.pdf", "TechGear Distributors", "TG-2026-0511",
                "PO-5009", _items(("Laptop (TechGear TG-500)", 10, 65000),
                                  ("Wireless Headphones", 10, 3000)))
    # missing-tax: FastFreight / PO-5011 (stored ex-tax) → only tax_present fails.
    items = _items(("Inter-state freight (zero-rated)", 1, 200000))
    notax = InvoiceSpec("edge_missing_tax_fastfreight.pdf", "FastFreight Logistics",
                        "FF-2026-0614", "2026-06-14", "PO-5011", items, "none", None,
                        subtotal=200000.0, tax_amount=None, total=200000.0)
    return [over, notax]


def main() -> None:
    gen._VENDOR_META.update(_DEMO_VENDOR_META)
    for sub, specs in (("batch", batch_specs()), ("edges", edge_specs())):
        out = DEMO_DIR / sub
        out.mkdir(parents=True, exist_ok=True)
        for spec in specs:
            (out / spec.filename).write_bytes(_render_pdf_bytes(spec))
            print(f"  demo/{sub}/{spec.filename:34s} {spec.invoice_number:14s} {spec.vendor_name}")
    print("\nBatch = straight-through (3 APPROVE + 2 REJECT); edges = flags to upload one at a time.")


if __name__ == "__main__":
    main()

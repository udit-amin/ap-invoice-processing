"""V1 invoice generator — produces the 4 missing normal PDFs for the v1 test matrix.

The 5 edge-case PDFs and normal_1_dell.pdf already exist; only normals 2-5 need
generating.  This generator is self-contained: it does NOT import from seed.py
because the v1 vendors are not in the old SQLite dataset.

Run with:  python -m app.generate.invoice_generator_v1
"""
from __future__ import annotations

from app.generate.invoice_generator import (
    InvoiceSpec, LineItem, write_invoice, inr,
    _separated,
)
from app import config

# GSTIN-style IDs and payment terms for the v1 vendor set.
_V1_VENDOR_META: dict[str, tuple[str, str]] = {
    "Stellar Software Solutions":         ("27BBBBB0001B1Z5", "Net-30"),
    "FastFreight Logistics":              ("24CCCCC0002C1Z4", "Net-45"),
    "Apex Consulting Group":              ("29DDDDD0003D1Z3", "Net-30"),
    "Nimbus Networks":                    ("36EEEEE0004E1Z2", "Net-30"),
}


def _items(*triples) -> list[LineItem]:
    return [LineItem(d, q, u, q * u) for (d, q, u) in triples]


def _patch_vendor_meta(name: str) -> None:
    """Temporarily inject a v1 vendor into the generator's meta lookup."""
    from app.generate import invoice_generator as _gen
    if name not in _gen._VENDOR_META and name in _V1_VENDOR_META:
        _gen._VENDOR_META[name] = _V1_VENDOR_META[name]


def build_v1_missing_specs() -> list[InvoiceSpec]:
    """Return specs only for the PDFs that do not yet exist."""
    specs: list[InvoiceSpec] = []

    # normal_2 — Stellar Software Solutions / PO-5005 — text, two lines
    items = _items(
        ("Analytics Suite annual license", 20, 9500),
        ("Onboarding & setup",              1, 35000),
    )
    sub = sum(i.line_total for i in items)
    tax, tot = _separated(sub)
    specs.append(InvoiceSpec(
        "normal_2_stellar.pdf", "Stellar Software Solutions", "SSS-2026-1187",
        "2026-06-01", "PO-5005", items, "separated", 18, sub, tax, tot,
    ))

    # normal_3 — FastFreight Logistics / PO-5004 — text, two lines
    items = _items(
        ("Full-truck freight (BLR-PNQ)", 1, 48000),
        ("Handling & insurance",          1,  6000),
    )
    sub = sum(i.line_total for i in items)
    tax, tot = _separated(sub)
    specs.append(InvoiceSpec(
        "normal_3_fastfreight.pdf", "FastFreight Logistics", "FF/INV/8841",
        "2026-05-30", "PO-5004", items, "separated", 18, sub, tax, tot,
    ))

    # normal_4 — Apex Consulting Group / PO-5007 — text, two lines
    # Invoice uses "(hours)"; PO intentionally uses "(hrs)" to exercise fuzzy matching.
    items = _items(
        ("Senior consultant (hours)", 60, 4500),
        ("Project manager (hours)",   20, 6000),
    )
    sub = sum(i.line_total for i in items)
    tax, tot = _separated(sub)
    specs.append(InvoiceSpec(
        "normal_4_apex.pdf", "Apex Consulting Group", "APX-2026-0098",
        "2026-06-03", "PO-5007", items, "separated", 18, sub, tax, tot,
    ))

    # normal_5 — Nimbus Networks / PO-5008 — text, two lines
    # Invoice says "Leased line 100 Mbps - May 2026"; PO uses "Leased line 100 Mbps".
    items = _items(
        ("Leased line 100 Mbps - May 2026", 1, 85000),
        ("Static IP block",                  1,  5000),
    )
    sub = sum(i.line_total for i in items)
    tax, tot = _separated(sub)
    specs.append(InvoiceSpec(
        "normal_5_nimbus.pdf", "Nimbus Networks", "NN-2026-05-2231",
        "2026-05-31", "PO-5008", items, "separated", 18, sub, tax, tot,
    ))

    return specs


def generate_missing() -> list[str]:
    paths = []
    for spec in build_v1_missing_specs():
        dest = config.INPUTS_DIR / spec.filename
        if dest.exists():
            print(f"  [exists ] {spec.filename}")
            continue
        _patch_vendor_meta(spec.vendor_name)
        path = write_invoice(spec)
        print(f"  [written] {spec.filename:32s}  {spec.vendor_name}")
        paths.append(path)
    return paths


if __name__ == "__main__":
    print(f"Generating missing v1 invoices into {config.INPUTS_DIR} ...")
    paths = generate_missing()
    if paths:
        print(f"Done — {len(paths)} PDF(s) written.")
    else:
        print("All v1 invoices already exist.")

"""Mint FRESH-numbered invoices for the live-upload demo moment.

The seeded history (scripts/seed_demo_history.py) already populates the dashboard
and review queue. But re-uploading any *seeded* fixture returns REJECT (duplicate)
by design — useless for showing a clean APPROVE. So this emits a few invoices with
brand-new invoice numbers, mapped to the real seeded vendors/POs so the verdicts
are deterministic:

    DELL/2026/0614  → Dell / PO-5001, total = PO exactly        → APPROVE  (happy path)
    GLX/2026/0614   → Globex (unapproved vendor) / PO-5002       → REJECT   (edge)
    TG/2026/0614    → TechGear / PO-5009, total ₹8.02L > ceiling → FLAG     (edge, over-authority)

They land in data/demo_live/. Keep them on the presenter's laptop and drag them
into the deployed UI's Run view. Reseed history (which resets PO balances) before
the real take so a rehearsal draw-down can't downgrade the live APPROVE.

    .venv/bin/python scripts/make_live_demo_invoices.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config                                              # noqa: E402
# Reuse the real generator's spec model + rendering core (internal helpers are
# fine here — this is a sibling demo script, not production code).
from app.generate.invoice_generator import (                        # noqa: E402
    InvoiceSpec, LineItem, _render_pdf_bytes, _separated,
)

DEMO_LIVE_DIR = config.DATA_DIR / "demo_live"


def _items(*triples) -> list[LineItem]:
    return [LineItem(d, q, u, q * u) for (d, q, u) in triples]


def build_demo_specs() -> list[InvoiceSpec]:
    specs: list[InvoiceSpec] = []

    # APPROVE — Dell / PO-5001. Lines + total match the PO exactly (₹5,66,400),
    # under the ₹7,50,000 ceiling, approved vendor, open PO → auto-APPROVE.
    items = _items(("Latitude 5440 Laptop", 5, 72000), ('UltraSharp 24" Monitor', 5, 24000))
    sub = sum(i.line_total for i in items)
    tax, tot = _separated(sub)
    specs.append(InvoiceSpec(
        "demo_approve_dell.pdf", "Dell Technologies India Pvt Ltd", "DELL/2026/0614",
        "2026-06-14", "PO-5001", items, "separated", 18, sub, tax, tot,
    ))

    # REJECT — Globex is an unapproved vendor (V-010); any amount rejects on the
    # vendor_approved check regardless of the PO.
    items = _items(("Professional services — June", 1, 80000))
    sub = sum(i.line_total for i in items)
    tax, tot = _separated(sub)
    specs.append(InvoiceSpec(
        "demo_reject_globex.pdf", "Globex Corporation", "GLX/2026/0614",
        "2026-06-14", "PO-5002", items, "separated", 18, sub, tax, tot,
    ))

    # FLAG — TechGear / PO-5009. Matches the PO (₹8,02,400) but exceeds the
    # ₹7,50,000 auto-approve ceiling → over-authority FLAG (all checks pass).
    items = _items(("Laptop (TechGear TG-500)", 10, 65000), ("Wireless Headphones", 10, 3000))
    sub = sum(i.line_total for i in items)
    tax, tot = _separated(sub)
    specs.append(InvoiceSpec(
        "demo_flag_techgear.pdf", "TechGear Distributors", "TG/2026/0614",
        "2026-06-14", "PO-5009", items, "separated", 18, sub, tax, tot,
    ))

    return specs


def main() -> None:
    DEMO_LIVE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Writing live-demo invoices into {DEMO_LIVE_DIR} ...")
    for spec in build_demo_specs():
        (DEMO_LIVE_DIR / spec.filename).write_bytes(_render_pdf_bytes(spec))
        print(f"  [written] {spec.filename:26s} {spec.invoice_number:16s} {spec.vendor_name}")
    print("Done. Drag these into the Run view for the live happy-path + edge-case demo.")


if __name__ == "__main__":
    main()

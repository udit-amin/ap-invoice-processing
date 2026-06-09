"""Load reference data (POs + vendors) from Postgres into the dict/list shapes
the check functions expect.

Money columns are NUMERIC in the database (psycopg returns Decimal); they are
cast to float here so checks.py / matcher.py keep doing plain float math.
"""
from __future__ import annotations

from src.db.connection import cursor


def _f(v) -> float | None:
    return float(v) if v is not None else None


def load_po_database() -> dict[str, dict]:
    """Return {po_id: {..., "expected_line_items": [{description, quantity, unit_price}]}}."""
    with cursor() as cur:
        cur.execute(
            """SELECT po_id, vendor_id, vendor_name, description,
                      approved_amount, remaining_balance, status, tolerance_pct
               FROM purchase_orders"""
        )
        pos = {
            po_id: {
                "po_id": po_id,
                "vendor_id": vendor_id,
                "vendor_name": vendor_name,
                "description": description,
                "approved_amount": _f(approved_amount),
                "remaining_balance": _f(remaining_balance),
                "status": status,
                "tolerance_pct": _f(tolerance_pct),
                "expected_line_items": [],
            }
            for (po_id, vendor_id, vendor_name, description, approved_amount,
                 remaining_balance, status, tolerance_pct) in cur.fetchall()
        }

        cur.execute(
            """SELECT po_id, description, quantity, unit_price
               FROM po_line_items
               ORDER BY po_id, line_no"""
        )
        for po_id, description, quantity, unit_price in cur.fetchall():
            if po_id in pos:
                pos[po_id]["expected_line_items"].append({
                    "description": description,
                    "quantity": _f(quantity),
                    "unit_price": _f(unit_price),
                })
    return pos


def load_vendor_registry() -> list[dict]:
    """Return a list of {vendor_id, vendor_name, approved, category} dicts."""
    with cursor() as cur:
        cur.execute(
            "SELECT vendor_id, vendor_name, approved, category FROM vendors ORDER BY vendor_id"
        )
        return [
            {
                "vendor_id": vendor_id,
                "vendor_name": vendor_name,
                "approved": approved,
                "category": category,
            }
            for (vendor_id, vendor_name, approved, category) in cur.fetchall()
        ]

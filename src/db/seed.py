"""Create data/invoices.db and seed it with reference data.

Idempotent — schema.sql drops and recreates all tables on each run.

Run with:  python -m src.db.seed
"""
from __future__ import annotations

import sqlite3

from src import config

# (vendor_id, vendor_name, approved, category, tax_id, terms)
VENDORS = [
    ("V-01", "Dell Technologies",     1, "Hardware",        "29AAAAA0001A1Z5", "Net-30"),
    ("V-02", "Logitech India",        1, "Peripherals",     "29AAAAA0002A1Z4", "Net-30"),
    ("V-03", "CloudHost Solutions",   1, "Hosting/SaaS",    "27AAAAA0003A1Z3", "Net-15"),
    ("V-04", "Acme Office Supplies",  1, "Office supplies", "06AAAAA0004A1Z2", "Net-30"),
    ("V-05", "FastFreight Logistics", 1, "Logistics",       "24AAAAA0005A1Z1", "Net-45"),
    ("V-06", "Surya Stationers",      1, "Stationery",      "33AAAAA0006A1Z0", "Net-15"),
    ("V-07", "BlueOak Furniture",     1, "Furniture",       "29AAAAA0007A1Z9", "Net-30"),
    ("V-08", "Nimbus Software Labs",  1, "Software",        "36AAAAA0008A1Z8", "Net-30"),
    ("V-09", "Quanta Networks",       1, "Networking",      "29AAAAA0009A1Z7", "Net-30"),
    # V-10 intentionally unapproved — reserved for future validation tests.
    ("V-10", "GreenLeaf Catering",    0, "Catering",        "29AAAAA0010A1Z6", "Net-15"),
]

# (po_id, vendor_name, description, approved_amount, remaining_balance, status, tolerance_pct)
PURCHASE_ORDERS = [
    ("PO-4421", "Dell Technologies",     "Laptop + headphone bundle x5", 450000, 450000, "open",   5),
    ("PO-4422", "Logitech India",        "Wireless mice & keyboards",     85000,  85000, "open",   5),
    ("PO-4423", "CloudHost Solutions",   "Annual hosting",               240000, 240000, "open",   3),
    ("PO-4424", "Acme Office Supplies",  "Office consumables",            60000,  60000, "open",   8),
    ("PO-4425", "FastFreight Logistics", "Q2 shipping",                  180000, 120000, "open",   5),
    ("PO-4426", "BlueOak Furniture",     "Workstation desks x8",         320000, 320000, "open",   5),
    ("PO-4427", "Nimbus Software Labs",  "Software licenses x20",        150000, 150000, "open",   3),
    # PO-4428 intentionally closed with zero balance — reserved for future validation tests.
    ("PO-4428", "Quanta Networks",       "Switches & routers",           275000,      0, "closed", 5),
    ("PO-4429", "Surya Stationers",      "Stationery (tax-inclusive)",    40000,  40000, "open",   8),
]

# (id, auto_approve_ceiling, default_tolerance_pct, confidence_threshold)
# Governance lives in this table, never hardcoded in application logic.
POLICY = (1, 50000.0, 5.0, config.CONFIDENCE_THRESHOLD)


def seed(db_path=None) -> None:
    db_path = db_path or config.DB_PATH
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(config.SCHEMA_PATH.read_text())
        conn.executemany("INSERT INTO vendors VALUES (?, ?, ?, ?, ?, ?)", VENDORS)
        conn.executemany("INSERT INTO purchase_orders VALUES (?, ?, ?, ?, ?, ?, ?)", PURCHASE_ORDERS)
        conn.execute("INSERT INTO policy_config VALUES (?, ?, ?, ?)", POLICY)
        conn.commit()
    finally:
        conn.close()


def _summary(db_path=None) -> None:
    db_path = db_path or config.DB_PATH
    conn = sqlite3.connect(db_path)
    try:
        v       = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
        approved = conn.execute("SELECT COUNT(*) FROM vendors WHERE approved=1").fetchone()[0]
        po      = conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0]
        closed  = conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE status='closed'").fetchone()[0]
        runs    = conn.execute("SELECT COUNT(*) FROM run_log").fetchone()[0]
        pol     = conn.execute("SELECT COUNT(*) FROM policy_config").fetchone()[0]
    finally:
        conn.close()
    print(f"Seeded {db_path}")
    print(f"  vendors:        {v} ({approved} approved, {v - approved} unapproved)")
    print(f"  purchase_orders:{po} ({closed} closed)")
    print(f"  run_log:        {runs} rows")
    print(f"  policy_config:  {pol} row")


if __name__ == "__main__":
    seed()
    _summary()

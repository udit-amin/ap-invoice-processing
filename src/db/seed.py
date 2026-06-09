"""Apply the Postgres schema and seed reference data.

Idempotent — schema uses CREATE TABLE IF NOT EXISTS and seeds use
INSERT ... ON CONFLICT DO NOTHING.  Reference data (vendors, POs + expected
line items, policy) is the v2 dataset that previously lived in the JSON
fixtures; it now lives here as the single seed source.

Run with:  python -m src.db.seed
"""
from __future__ import annotations

from src import config
from src.db.connection import apply_schema, cursor

# (vendor_id, vendor_name, approved, category)
VENDORS = [
    ("V-001", "Dell Technologies India Pvt Ltd",  True,  "Hardware"),
    ("V-002", "Stellar Software Solutions",        True,  "Software"),
    ("V-003", "FastFreight Logistics",             True,  "Logistics"),
    ("V-004", "Apex Consulting Group",             True,  "Consulting"),
    ("V-005", "Nimbus Networks",                   True,  "Networking"),
    ("V-006", "GreenLeaf Facilities Management",   True,  "Facilities"),
    ("V-007", "TechGear Distributors",             True,  "Hardware"),
    ("V-008", "BluePrint Marketing",               True,  "Marketing"),
    ("V-009", "Quanta Networks",                   True,  "Networking"),
    # V-010 intentionally unapproved — wired for a future fraud edge case.
    ("V-010", "Globex Corporation",                False, "Unknown"),
]

# (po_id, vendor_id, vendor_name, description, approved_amount,
#  remaining_balance, status, tolerance_pct, [ (description, qty, unit_price), ... ])
PURCHASE_ORDERS = [
    ("PO-5001", "V-001", "Dell Technologies India Pvt Ltd",
     "Laptop and monitor procurement", 566400, 566400, "open", 3, [
        ("Latitude 5440 Laptop", 5, 72000),
        ('UltraSharp 24" Monitor', 5, 24000),
     ]),
    ("PO-5002", "V-010", "Globex Corporation",
     "Reserved — unapproved vendor test slot", 100000, 100000, "open", 5, []),
    ("PO-5003", "V-009", "Quanta Networks",
     "Networking equipment — closed PO for future validation test",
     275000, 0, "closed", 5, []),
    ("PO-5004", "V-003", "FastFreight Logistics",
     "Q2 freight and handling", 63720, 63720, "open", 5, [
        ("Full-truck freight (BLR-PNQ)", 1, 48000),
        ("Handling & insurance", 1, 6000),
     ]),
    ("PO-5005", "V-002", "Stellar Software Solutions",
     "Analytics platform license + onboarding", 265500, 265500, "open", 5, [
        ("Analytics Suite annual license", 20, 9500),
        ("Onboarding & setup", 1, 35000),
     ]),
    ("PO-5006", "V-006", "GreenLeaf Facilities Management",
     "Monthly facility management services — May 2026", 97940, 97940, "open", 5, [
        ("Monthly Housekeeping - May", 1, 65000),
        ("Deep Cleaning (one-time)", 1, 18000),
     ]),
    ("PO-5007", "V-004", "Apex Consulting Group",
     "Consulting engagement — Q2 2026", 460200, 460200, "open", 5, [
        # Abbreviated "(hrs)" — invoice uses "(hours)"; exercises fuzzy matching.
        ("Senior consultant (hrs)", 60, 4500),
        ("Project manager (hrs)", 20, 6000),
     ]),
    ("PO-5008", "V-005", "Nimbus Networks",
     "Leased line and static IP — May 2026", 106200, 106200, "open", 5, [
        ("Leased line 100 Mbps", 1, 85000),
        ("Static IP block", 1, 5000),
     ]),
    ("PO-5009", "V-007", "TechGear Distributors",
     "Laptop and headphone bundle procurement", 802400, 802400, "open", 5, [
        ("Laptop (TechGear TG-500)", 10, 65000),
        ("Wireless Headphones", 10, 3000),
     ]),
    ("PO-5010", "V-008", "BluePrint Marketing",
     "Q2 marketing campaign and creative retainer", 354000, 354000, "open", 5, [
        # Ex-tax values; invoice presents tax-inclusive per-line prices.
        ("Social media campaign (Q2)", 1, 200000),
        ("Creative design retainer", 1, 100000),
     ]),
]

# (id, auto_approve_ceiling, default_tolerance_pct, confidence_threshold)
POLICY = (1, 50000.0, 5.0, float(config.CONFIDENCE_THRESHOLD))


def seed() -> None:
    apply_schema()
    with cursor(autocommit=False) as cur:
        cur.executemany(
            """INSERT INTO vendors (vendor_id, vendor_name, approved, category)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (vendor_id) DO NOTHING""",
            VENDORS,
        )

        for (po_id, vendor_id, vendor_name, desc, approved, remaining,
             status, tol, lines) in PURCHASE_ORDERS:
            cur.execute(
                """INSERT INTO purchase_orders
                   (po_id, vendor_id, vendor_name, description,
                    approved_amount, remaining_balance, status, tolerance_pct)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (po_id) DO NOTHING""",
                (po_id, vendor_id, vendor_name, desc, approved, remaining, status, tol),
            )
            # Replace line items so reseeding stays consistent with the PO.
            cur.execute("DELETE FROM po_line_items WHERE po_id = %s", (po_id,))
            for line_no, (ldesc, qty, price) in enumerate(lines, 1):
                cur.execute(
                    """INSERT INTO po_line_items
                       (po_id, line_no, description, quantity, unit_price)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (po_id, line_no, ldesc, qty, price),
                )

        cur.execute(
            """INSERT INTO policy_config
               (id, auto_approve_ceiling, default_tolerance_pct, confidence_threshold)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            POLICY,
        )
        cur.connection.commit()


def _summary() -> None:
    with cursor() as cur:
        cur.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE approved) FROM vendors")
        v, approved = cur.fetchone()
        cur.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE status='closed') FROM purchase_orders")
        po, closed = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM po_line_items")
        (lines,) = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM policy_config")
        (pol,) = cur.fetchone()
    print(f"Seeded {config.get_database_url()}")
    print(f"  vendors:         {v} ({approved} approved, {v - approved} unapproved)")
    print(f"  purchase_orders: {po} ({closed} closed)")
    print(f"  po_line_items:   {lines}")
    print(f"  policy_config:   {pol} row")


if __name__ == "__main__":
    seed()
    _summary()

"""The seven validation checks.  Each returns a check-result dict:

    {"check": str, "status": "pass"|"fail"|"skip", "reason": str}

check_line_reconciliation also includes a "detail" key when it runs fully.
check_duplicate returns (check_result, updated_run_log).
check_po_lookup returns (check_result, matched_po_dict | None).

None of these functions ever sets an approve/reject/decision field.
"""
from __future__ import annotations

from app.db.connection import cursor
from app.validate import matcher


# ---------------------------------------------------------------------------
# 1. PO lookup
# ---------------------------------------------------------------------------

def check_po_lookup(
    po_reference: str | None,
    po_db: dict[str, dict],
) -> tuple[dict, dict | None]:
    if not po_reference:
        return (
            {"check": "po_lookup", "status": "fail",
             "reason": "No PO reference on invoice"},
            None,
        )
    po = po_db.get(po_reference)
    if po is None:
        return (
            {"check": "po_lookup", "status": "fail",
             "reason": f"{po_reference} not found in PO database"},
            None,
        )
    return (
        {"check": "po_lookup", "status": "pass",
         "reason": f"{po_reference} found in database"},
        po,
    )


# ---------------------------------------------------------------------------
# 2. Vendor approval
# ---------------------------------------------------------------------------

def check_vendor_approval(
    vendor_name: str | None,
    registry: list[dict],
) -> dict:
    if not vendor_name:
        return {
            "check": "vendor_approved", "status": "fail",
            "reason": "No vendor name on invoice",
        }
    entry = matcher.find_vendor(vendor_name, registry)
    if entry is None:
        return {
            "check": "vendor_approved", "status": "fail",
            "reason": f"Vendor '{vendor_name}' not found in registry",
        }
    if not entry.get("approved"):
        return {
            "check": "vendor_approved", "status": "fail",
            "reason": (
                f"Vendor '{entry['vendor_name']}' ({entry['vendor_id']}) "
                "is not approved"
            ),
        }
    return {
        "check": "vendor_approved", "status": "pass",
        "reason": (
            f"'{entry['vendor_name']}' is approved ({entry['vendor_id']})"
        ),
    }


# ---------------------------------------------------------------------------
# 3. PO status
# ---------------------------------------------------------------------------

def check_po_status(po: dict) -> dict:
    status = po.get("status", "").lower()
    if status == "open":
        return {
            "check": "po_status", "status": "pass",
            "reason": f"PO {po['po_id']} is open",
        }
    return {
        "check": "po_status", "status": "fail",
        "reason": f"PO {po['po_id']} is {status}",
    }


# ---------------------------------------------------------------------------
# 4. Total tolerance
# ---------------------------------------------------------------------------

def check_total_tolerance(
    invoice_total: float | None,
    po: dict,
) -> dict:
    if invoice_total is None:
        return {
            "check": "total_tolerance", "status": "fail",
            "reason": "Invoice total is missing",
        }
    balance   = po.get("remaining_balance", 0)
    tol_pct   = po.get("tolerance_pct", 0)
    po_id     = po["po_id"]

    if balance == 0:
        if invoice_total == 0:
            return {
                "check": "total_tolerance", "status": "pass",
                "reason": f"Invoice total and PO balance are both zero ({po_id})",
            }
        return {
            "check": "total_tolerance", "status": "fail",
            "reason": (
                f"PO {po_id} has zero remaining balance; "
                f"invoice total is {invoice_total:,.2f}"
            ),
        }

    variance_pct = abs(invoice_total - balance) / balance * 100
    if variance_pct <= tol_pct:
        return {
            "check": "total_tolerance", "status": "pass",
            "reason": (
                f"Invoice total within {variance_pct:.1f}% of PO balance "
                f"(allowed {tol_pct}%)"
            ),
        }
    return {
        "check": "total_tolerance", "status": "fail",
        "reason": (
            f"Invoice total {invoice_total:,.2f} is {variance_pct:.1f}% "
            f"from PO balance {balance:,.2f} (allowed {tol_pct}%)"
        ),
    }


# ---------------------------------------------------------------------------
# 5. Line-item reconciliation
# ---------------------------------------------------------------------------

def check_line_reconciliation(
    extracted: dict,
    po: dict,
) -> dict:
    po_id     = po["po_id"]
    po_lines  = po.get("expected_line_items", [])
    inv_lines = extracted.get("line_items", [])
    tax       = extracted.get("tax", {}) or {}
    treatment = tax.get("treatment")
    rate_pct  = tax.get("rate_pct")
    tol_pct   = po.get("tolerance_pct", 0)

    # Bundled path
    if any(li.get("is_bundle") for li in inv_lines):
        return {
            "check": "line_reconciliation", "status": "skip",
            "reason": (
                "Validated at total level; line detail unavailable (bundled invoice)"
            ),
        }

    embedded = (treatment == "embedded")

    # Embedded tax with unknown rate — cannot safely derive ex-tax
    if embedded and rate_pct is None:
        return {
            "check": "line_reconciliation", "status": "skip",
            "reason": (
                "Tax treatment is embedded but rate is unknown; "
                "cannot derive ex-tax unit prices — validated at subtotal level"
            ),
        }

    if not po_lines:
        return {
            "check": "line_reconciliation", "status": "skip",
            "reason": f"PO {po_id} has no expected line items to reconcile against",
        }

    detail = matcher.match_lines(
        inv_lines, po_lines, tol_pct,
        tax_rate=rate_pct if embedded else None,
        embedded_tax=embedded,
    )

    mismatches = [
        d for d in detail
        if d["classification"] != "exact_match"
    ]

    if not mismatches:
        return {
            "check": "line_reconciliation", "status": "pass",
            "reason": f"All {len(detail)} line(s) match PO {po_id}",
            "detail": detail,
        }

    fail_count = len(mismatches)
    total_count = len(detail)
    embedded_note = " (prices derived ex-tax)" if embedded else ""
    return {
        "check": "line_reconciliation", "status": "fail",
        "reason": (
            f"{fail_count} of {total_count} line(s) mismatch{embedded_note}"
        ),
        "detail": detail,
    }


# ---------------------------------------------------------------------------
# 6. Duplicate detection
# ---------------------------------------------------------------------------

def check_duplicate(
    invoice_number: str | None,
    vendor_name: str | None,
    run_id: str | None = None,
) -> dict:
    """Race-proof duplicate detection via a UNIQUE(invoice_number, vendor_name)
    insert. The first run to insert wins (pass); any later run loses the
    conflict (fail). Because the database arbitrates atomically, two concurrent
    runs of the same invoice cannot both pass.
    """
    if not invoice_number:
        return {
            "check": "duplicate", "status": "fail",
            "reason": "Invoice number missing — cannot check for duplicates",
        }

    vendor = vendor_name or ""
    with cursor() as cur:
        cur.execute(
            """INSERT INTO invoices (invoice_number, vendor_name, first_run_id)
               VALUES (%s, %s, %s)
               ON CONFLICT (invoice_number, vendor_name) DO NOTHING
               RETURNING invoice_id""",
            (invoice_number, vendor, run_id),
        )
        inserted = cur.fetchone()

        if inserted:
            return {
                "check": "duplicate", "status": "pass",
                "reason": f"First occurrence of invoice {invoice_number}",
            }

        # Conflict: an earlier run already recorded this invoice.
        cur.execute(
            """SELECT first_run_id, first_seen_at FROM invoices
               WHERE invoice_number = %s AND vendor_name = %s""",
            (invoice_number, vendor),
        )
        row = cur.fetchone()

    if row:
        first_run_id, first_seen = row
        when = first_seen.date().isoformat() if first_seen else "an earlier run"
        return {
            "check": "duplicate", "status": "fail",
            "reason": f"Duplicate of run {first_run_id} on {when}",
        }
    return {
        "check": "duplicate", "status": "fail",
        "reason": f"Duplicate of an earlier run of invoice {invoice_number}",
    }


# ---------------------------------------------------------------------------
# 7. Tax presence
# ---------------------------------------------------------------------------

def check_tax_present(extracted: dict) -> dict:
    """A pure *presence* check: does the invoice declare tax?

    This deliberately does NO amount arithmetic and never touches the PO or line
    items — `total_tolerance` (tax-inclusive totals) and `line_reconciliation`
    (ex-tax line prices, deriving ex-tax for embedded invoices) already own that
    math. Re-deriving "lines + tax = total" here would double-count the tax gap.

    So this only reads the extractor's tax classification:
      - "separated" / "embedded" -> tax is present  -> pass
      - "none"                    -> tax is absent   -> fail (a FLAG signal)
      - null / unknown            -> not determined  -> skip (no escalation;
        e.g. the answer-key dry-run path, which doesn't model tax)
    """
    treatment = (extracted.get("tax") or {}).get("treatment")
    if treatment in ("separated", "embedded"):
        return {
            "check": "tax_present", "status": "pass",
            "reason": f"Invoice includes {treatment} tax",
        }
    if treatment == "none":
        return {
            "check": "tax_present", "status": "fail",
            "reason": "Invoice shows no tax — tax must be included",
        }
    return {
        "check": "tax_present", "status": "skip",
        "reason": "Tax presence could not be determined from the extraction",
    }

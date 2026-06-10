"""End-to-end pipeline harness.

For each invoice:
  1. Extracts (live, via the pipeline) or loads the answer-key JSON (--dry-run).
  2. Runs the full governance pipeline (ingest -> extract -> match -> validate
     -> decide), persisting events, report, and verdict to Postgres.
  3. Prints the check matrix + the Verdict column, then per-invoice verdict/reason.

The §7 verdict matrix is per-invoice-in-isolation, so PO balances are reseeded
before each invoice — otherwise one invoice's APPROVE decrement would change a
later invoice's verdict (e.g. normal_1 and edge_4 both bill PO-5001). The
balance decrement + race downgrade are exercised by tests/test_decision.py and
live /process.

By default the harness also resets operational state (runs/verdicts/events/
reports) first so the run is reproducible; pass --keep to accumulate (and watch
duplicates flip to REJECT).

Usage:
    python validate_all.py              # live extraction via the pipeline
    python validate_all.py --dry-run    # answer-key extraction, no LLM call
    python validate_all.py --dry-run --keep
"""
from __future__ import annotations

import argparse
import json
import sys

from src import config
from src.db.connection import cursor
from src.db import seed as _seed
from src.governance import recorder
from src.validate.validator import validate
from src.decide import engine, commit
from src.decide.policy import load_policy
from src import pipeline

INVOICES = [
    "normal_1_dell.pdf",
    "normal_2_stellar.pdf",
    "normal_3_fastfreight.pdf",
    "normal_4_apex.pdf",
    "normal_5_nimbus.pdf",
    "edge_1_greenleaf_scanned.pdf",
    "edge_2_techgear_bundled.pdf",
    "edge_3_blueprint_embedded_tax.pdf",
    "edge_4_dell_line_mismatch.pdf",
    "edge_5_globex_unapproved.pdf",
    "edge_6_cloudhost_closed_po.pdf",
]

ANSWER_KEY_PATH = config.DATA_DIR / "expected_extraction_answer_key.json"

CHECK_ORDER = [
    "po_lookup", "vendor_approved", "po_status",
    "total_tolerance", "line_reconciliation", "duplicate",
]

_ICONS = {"pass": "✓", "fail": "✗", "skip": "–"}
_VERDICT_ICON = {"APPROVE": "APPROVE ✓", "FLAG": "FLAG ⚑", "REJECT": "REJECT ✗"}

# Confidence assigned in dry-run: below the policy gate when the invoice is a
# low-confidence (scanned) fixture, comfortably above otherwise.
_LOW_CONF, _HIGH_CONF = 0.62, 0.95


def _answer_key_to_extracted(filename: str, ak: dict) -> dict:
    """Convert an answer-key entry to the extractor output schema shape."""
    entry = ak.get(filename, {})
    raw_lines = entry.get("line_items", [])
    tax_mode = entry.get("tax_mode", "separate")
    is_bundled = entry.get("is_bundled", False)
    overall = _HIGH_CONF if entry.get("expect_high_confidence", True) else _LOW_CONF

    line_items = []
    for li in raw_lines:
        up = li.get("unit_price") or li.get("unit_price_incl_tax")
        qty = li.get("qty")
        line_items.append({
            "description":       li.get("description", ""),
            "quantity":          qty,
            "unit_price":        up,
            "line_total":        li.get("amount") or (qty * up if qty and up else None),
            "is_bundle":         is_bundled,
            "bundle_components": [],
        })

    return {
        "source_type":    entry.get("source_type", "text"),
        "invoice_number": entry.get("invoice_number"),
        "vendor_name":    entry.get("vendor_name"),
        "invoice_date":   entry.get("invoice_date"),
        "po_reference":   entry.get("po_reference"),
        "currency":       entry.get("currency", "INR"),
        "line_items":     line_items,
        "subtotal":       entry.get("subtotal") or entry.get("subtotal_ex_tax"),
        "tax": {
            "amount":    entry.get("tax_amount"),
            "rate_pct":  18,
            "treatment": "embedded" if tax_mode == "embedded" else "separated",
        },
        "total":         entry.get("total"),
        "extraction_confidence": {
            "invoice_number": overall, "vendor_name": overall,
            "po_reference": overall, "total": overall, "overall": overall,
        },
        "extraction_notes": [],
        "error": None,
    }


def _reset_operational_state() -> None:
    with cursor() as cur:
        cur.execute(
            "TRUNCATE governance_events, validation_reports, verdicts, invoices, "
            "pipeline_runs RESTART IDENTITY CASCADE"
        )


def _reset_po_balances() -> None:
    """Restore every PO's balance/status to the seeded baseline (per-invoice
    isolation for the matrix)."""
    with cursor() as cur:
        for po in _seed.PURCHASE_ORDERS:
            cur.execute(
                "UPDATE purchase_orders SET remaining_balance = %s, status = %s "
                "WHERE po_id = %s",
                (po[5], po[6], po[0]),
            )


def _status_cell(report: dict, check_name: str) -> str:
    by_name = {c["check"]: c for c in report["checks"]}
    return _ICONS.get(by_name.get(check_name, {}).get("status", "?"), "?")


def _process_from_answer_key(filename: str, extracted: dict) -> tuple[dict, dict]:
    """Mirror pipeline.process_invoice but with answer-key extraction."""
    overall = (extracted.get("extraction_confidence") or {}).get("overall")
    run_id = recorder.start_run(
        invoice_path=filename,
        invoice_number=extracted.get("invoice_number"),
        vendor_name=extracted.get("vendor_name"),
        po_reference=extracted.get("po_reference"),
        source_type=extracted.get("source_type"),
    )
    recorder.log_event(run_id, recorder.INGEST, recorder.OK, {"invoice_path": filename})
    recorder.log_event(run_id, recorder.EXTRACT, recorder.OK,
                       {"source_type": extracted.get("source_type"), "overall_conf": overall})
    report = validate(extracted, run_id=run_id)
    verdict = engine.decide(report, extracted, load_policy())
    decision = commit.commit_decision(
        verdict, report.get("matched_po"), extracted.get("total"), run_id)
    recorder.finish_run(run_id, overall_conf=overall)
    return report, decision


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Use answer-key JSON instead of calling the extractor")
    parser.add_argument("--keep", action="store_true",
                        help="Do not reset operational state before running")
    args = parser.parse_args()

    use_api = (not args.dry_run) and (config.get_api_key() is not None)
    if not use_api:
        print("[harness] Using answer-key JSON (no extractor call).\n")

    answer_key = {}
    if not use_api:
        if not ANSWER_KEY_PATH.exists():
            print(f"[harness] Answer key not found at {ANSWER_KEY_PATH}", file=sys.stderr)
            sys.exit(1)
        answer_key = json.loads(ANSWER_KEY_PATH.read_text())

    if not args.keep:
        _reset_operational_state()
        print("[harness] Operational state reset (runs/verdicts/events/reports).\n")

    if use_api:
        from src.generate.invoice_generator_v1 import generate_missing
        generate_missing()

    col_w = 34
    hdr = (f"{'Invoice':<{col_w}}"
           + "".join(f"{n[:8]:>10}" for n in CHECK_ORDER)
           + f"{'Verdict':>12}")
    print(hdr)
    print("-" * len(hdr))

    rows = []
    for filename in INVOICES:
        _reset_po_balances()  # judge each invoice against the seeded baseline
        pdf_path = config.INPUTS_DIR / filename
        if use_api:
            res = pipeline.process_invoice(str(pdf_path), invoice_path_label=filename)
            report, decision = res["validation"], res["decision"]
        else:
            extracted = _answer_key_to_extracted(filename, answer_key)
            report, decision = _process_from_answer_key(filename, extracted)

        rows.append((filename, report, decision))
        line = (f"{filename:<{col_w}}"
                + "".join(f"{_status_cell(report, n):>10}" for n in CHECK_ORDER)
                + f"{_VERDICT_ICON.get(decision['verdict'], decision['verdict']):>12}")
        print(line)

    print("-" * len(hdr))
    print("\nChecks: ✓ pass  ✗ fail  – skip      Verdict: ✓ approve  ⚑ flag  ✗ reject\n")

    for filename, report, decision in rows:
        print(f"  {filename} — {decision['verdict']}")
        print(f"      {decision['reason']}")


if __name__ == "__main__":
    main()

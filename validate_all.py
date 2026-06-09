"""End-to-end validation harness.

For each of the 9 v1 invoices:
  1. Extracts (live, via the pipeline) or loads the answer-key JSON (--dry-run).
  2. Runs the full governance pipeline (ingest -> extract -> match -> validate),
     persisting events + report to Postgres.
  3. Prints the check matrix and per-invoice failure detail.

By default the harness resets operational state (pipeline_runs, invoices,
validation_reports, governance_events) first so the matrix is reproducible;
pass --keep to accumulate across runs (and watch duplicates flip to fail).

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
from src.governance import recorder
from src.validate.validator import validate
from src import pipeline

V1_INVOICES = [
    "normal_1_dell.pdf",
    "normal_2_stellar.pdf",
    "normal_3_fastfreight.pdf",
    "normal_4_apex.pdf",
    "normal_5_nimbus.pdf",
    "edge_1_greenleaf_scanned.pdf",
    "edge_2_techgear_bundled.pdf",
    "edge_3_blueprint_embedded_tax.pdf",
    "edge_4_dell_line_mismatch.pdf",
]

ANSWER_KEY_PATH = config.DATA_DIR / "expected_extraction_answer_key.json"

CHECK_ORDER = [
    "po_lookup", "vendor_approved", "po_status",
    "total_tolerance", "line_reconciliation", "duplicate",
]

_ICONS = {"pass": "✓", "fail": "✗", "skip": "–"}


def _answer_key_to_extracted(filename: str, ak: dict) -> dict:
    """Convert an answer-key entry to the extractor output schema shape."""
    entry = ak.get(filename, {})
    raw_lines = entry.get("line_items", [])
    tax_mode = entry.get("tax_mode", "separate")
    is_bundled = entry.get("is_bundled", False)

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
            "invoice_number": 0.90, "vendor_name": 0.90,
            "po_reference": 0.90, "total": 0.90, "overall": 0.90,
        },
        "extraction_notes": [],
        "error": None,
    }


def _reset_operational_state() -> None:
    with cursor() as cur:
        cur.execute(
            "TRUNCATE governance_events, validation_reports, invoices, "
            "pipeline_runs RESTART IDENTITY CASCADE"
        )


def _status_cell(report: dict, check_name: str) -> str:
    by_name = {c["check"]: c for c in report["checks"]}
    return _ICONS.get(by_name.get(check_name, {}).get("status", "?"), "?")


def _validate_from_answer_key(filename: str, extracted: dict) -> dict:
    """Mirror pipeline.process_invoice but with answer-key extraction."""
    run_id = recorder.start_run(
        invoice_path=filename,
        invoice_number=extracted.get("invoice_number"),
        vendor_name=extracted.get("vendor_name"),
        po_reference=extracted.get("po_reference"),
        source_type=extracted.get("source_type"),
    )
    recorder.log_event(run_id, recorder.INGEST, recorder.OK, {"invoice_path": filename})
    recorder.log_event(run_id, recorder.EXTRACT, recorder.OK,
                       {"source_type": extracted.get("source_type"), "overall_conf": 0.90})
    report = validate(extracted, run_id=run_id)
    recorder.finish_run(run_id, overall_conf=0.90)
    return report


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
        print("[harness] Operational state reset (pipeline_runs/invoices/"
              "validation_reports/governance_events).\n")

    if use_api:
        from src.generate.invoice_generator_v1 import generate_missing
        generate_missing()

    col_w = 34
    hdr = f"{'Invoice':<{col_w}}" + "".join(f"{n[:8]:>12}" for n in CHECK_ORDER)
    print(hdr)
    print("-" * len(hdr))

    reports = []
    for filename in V1_INVOICES:
        pdf_path = config.INPUTS_DIR / filename
        if use_api:
            report = pipeline.process_invoice(str(pdf_path), invoice_path_label=filename)["validation"]
        else:
            extracted = _answer_key_to_extracted(filename, answer_key)
            report = _validate_from_answer_key(filename, extracted)

        reports.append((filename, report))
        row = f"{filename:<{col_w}}" + "".join(
            f"{_status_cell(report, n):>12}" for n in CHECK_ORDER
        )
        print(row)

    print("-" * len(hdr))
    print("\nLegend: ✓ pass   ✗ fail   – skip\n")

    for filename, report in reports:
        failed = [c for c in report["checks"] if c["status"] == "fail"]
        for c in failed:
            print(f"  {filename} — [{c['check']}] {c['reason']}")
            for d in c.get("detail", []):
                print(f"      • {d['classification']}: "
                      f"{d.get('invoice_line') or d.get('matched_po_line')}")


if __name__ == "__main__":
    main()

"""Landing → process → archive ingestion worker.

Simulates the AWS pickup worker: invoices land in one location, get run through
the pipeline, and are moved to an archive location **partitioned by processing
date (YYYYMMDD)**. Locally the two "buckets" are folders; on AWS they are S3
prefixes and this module is the worker's entrypoint (an S3-event Lambda or a
scheduled ECS task running the app image) — see docs/ARCHITECTURE.md.

It reuses `pipeline.process_invoice` (the single pipeline entry), so a swept
invoice gets the exact same extraction, validation, verdict, and governance
trail as one uploaded through the UI — just stamped as the **system** actor
(an automated run, not a person). A file that fails to process is left in the
landing area for retry (a real worker would route it to a dead-letter queue).

    python -m app.ingest.worker                      # data/landing → data/archive
    python -m app.ingest.worker --seed               # copy the demo fixtures into landing first
    python -m app.ingest.worker --landing X --archive Y
"""
from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app import config, pipeline

LANDING_DIR = config.DATA_DIR / "landing"
ARCHIVE_DIR = config.DATA_DIR / "archive"


def archive_destination(archive_root: Path, filename: str,
                        when: datetime | None = None) -> Path:
    """Where a processed file lands: ``<archive_root>/<YYYYMMDD>/<filename>``,
    partitioned by the processing date (UTC)."""
    day = (when or datetime.now(timezone.utc)).strftime("%Y%m%d")
    return archive_root / day / filename


def sweep(
    landing: Path,
    archive_root: Path,
    on_event: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Process every PDF in `landing`, then move each to its dated archive
    partition. Returns one result dict per file. A file that raises is left in
    place (not archived) so it can be retried."""
    results: list[dict[str, Any]] = []
    # rglob so a date-partitioned landing (landing/<YYYYMMDD>/…) works too,
    # mirroring the archive partition; a flat landing still matches.
    for pdf in sorted(landing.rglob("*.pdf")):
        if on_event:
            on_event(f"processing {pdf.name}")
        try:
            result = pipeline.process_invoice(str(pdf), invoice_path_label=pdf.name)
        except Exception as exc:  # noqa: BLE001 — failed file stays for retry/DLQ
            results.append({"file": pdf.name, "verdict": "ERROR",
                            "run_id": None, "archived_to": None, "error": str(exc)})
            continue

        dest = archive_destination(archive_root, pdf.name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(pdf), str(dest))
        results.append({
            "file": pdf.name,
            "verdict": (result.get("decision") or {}).get("verdict"),
            "run_id": result.get("run_id"),
            "archived_to": str(dest),
        })
    return results


def _seed_landing(landing: Path) -> int:
    """Copy the demo fixtures into the landing area (for a quick end-to-end demo)."""
    from validate_all import INVOICES
    landing.mkdir(parents=True, exist_ok=True)
    n = 0
    for name in INVOICES:
        src = config.INPUTS_DIR / name
        if src.exists():
            shutil.copy2(src, landing / name)
            n += 1
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description="Landing → process → archive invoice worker.")
    parser.add_argument("--landing", type=Path, default=LANDING_DIR)
    parser.add_argument("--archive", type=Path, default=ARCHIVE_DIR)
    parser.add_argument("--seed", action="store_true",
                        help="Copy the demo fixtures into the landing area first.")
    args = parser.parse_args()

    if args.seed:
        copied = _seed_landing(args.landing)
        print(f"[seed] copied {copied} fixture(s) into {args.landing}")

    if not args.landing.is_dir():
        print(f"Landing folder not found: {args.landing}")
        return

    pending = sorted(args.landing.rglob("*.pdf"))
    if not pending:
        print(f"Nothing to ingest in {args.landing}.")
        return

    print(f"Ingesting {len(pending)} file(s) from {args.landing} …\n")
    results = sweep(args.landing, args.archive, on_event=lambda m: print(f"  · {m}"))

    print(f"\n{'File':<34}{'Verdict':<10}Archived to")
    print("-" * 80)
    for r in results:
        where = r["archived_to"] or "(left in landing — failed)"
        print(f"{r['file']:<34}{(r['verdict'] or '—'):<10}{where}")
    archived = sum(1 for r in results if r["archived_to"])
    print(f"\nProcessed {len(results)}, archived {archived} "
          f"(partitioned by date under {args.archive}).")


if __name__ == "__main__":
    main()

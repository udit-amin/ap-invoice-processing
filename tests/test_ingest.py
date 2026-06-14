"""Ingest worker — date partitioning (pure) and the landing→archive sweep.

`pipeline.process_invoice` is monkeypatched, so the move/partition logic is
tested without a model or a database.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.ingest import worker


def test_archive_destination_partitions_by_date():
    when = datetime(2026, 6, 14, 9, 30, tzinfo=timezone.utc)
    assert worker.archive_destination(Path("/arc"), "inv.pdf", when=when) == \
        Path("/arc/20260614/inv.pdf")


def test_sweep_processes_then_archives(tmp_path, monkeypatch):
    landing = tmp_path / "landing"
    landing.mkdir()
    archive = tmp_path / "archive"
    (landing / "a.pdf").write_bytes(b"%PDF-1.4 a")
    (landing / "b.pdf").write_bytes(b"%PDF-1.4 b")

    def fake_process(path, invoice_path_label=None):
        return {"run_id": f"rid-{invoice_path_label}", "decision": {"verdict": "APPROVE"}}

    monkeypatch.setattr("app.ingest.worker.pipeline.process_invoice", fake_process)
    results = worker.sweep(landing, archive)

    assert {r["file"] for r in results} == {"a.pdf", "b.pdf"}
    assert all(r["verdict"] == "APPROVE" for r in results)
    assert not list(landing.glob("*.pdf"))  # moved out of landing
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    assert (archive / day / "a.pdf").exists()
    assert (archive / day / "b.pdf").exists()


def test_sweep_leaves_failed_file_in_landing(tmp_path, monkeypatch):
    landing = tmp_path / "landing"
    landing.mkdir()
    archive = tmp_path / "archive"
    (landing / "bad.pdf").write_bytes(b"%PDF-1.4 bad")

    def boom(path, invoice_path_label=None):
        raise RuntimeError("extraction blew up")

    monkeypatch.setattr("app.ingest.worker.pipeline.process_invoice", boom)
    results = worker.sweep(landing, archive)

    assert results[0]["verdict"] == "ERROR"
    assert results[0]["archived_to"] is None
    assert (landing / "bad.pdf").exists()  # left for retry / DLQ

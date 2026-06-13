"""UI translation layer (no infra). The api_client label maps + event→stage
mapping are pure presentation logic; this guards the run-view stage replay and
the decision-card driver phrasing without needing a server or DB.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

import api_client  # noqa: E402


def test_stage_sequence_is_the_seven_human_stages():
    keys = [k for k, _ in api_client.STAGE_SEQUENCE]
    assert keys == ["received", "read", "po", "vendor", "validated", "decided", "logged"]


def test_stages_from_events_resolves_statuses():
    events = [
        {"stage": "ingest", "status": "ok", "detail": {}},
        {"stage": "extract", "status": "ok", "detail": {"source_type": "text"}},
        {"stage": "match", "status": "ok", "detail": {"check": "po_lookup"}},
        {"stage": "match", "status": "ok", "detail": {"check": "vendor_approved"}},
        {"stage": "validate", "status": "fail", "detail": {"check": "line_reconciliation"}},
        {"stage": "validate", "status": "ok", "detail": {"check": "duplicate"}},
        {"stage": "decision", "status": "warn", "detail": {}},
    ]
    by_key = {s["key"]: s["status"] for s in api_client.stages_from_events(events)}
    assert by_key["received"] == "done"
    assert by_key["read"] == "done"
    assert by_key["po"] == "done"
    assert by_key["vendor"] == "done"
    assert by_key["validated"] == "fail"   # a validate-stage check failed
    assert by_key["decided"] == "done"     # warn maps to done (it ran)
    assert by_key["logged"] == "done"


def test_stages_from_events_all_pending_when_empty():
    by_key = {s["key"]: s["status"] for s in api_client.stages_from_events([])}
    assert set(by_key.values()) == {"pending"}


def test_label_maps_cover_the_known_signals():
    for sig in ("po_lookup", "vendor_approved", "over_authority", "low_confidence"):
        clean, fired = api_client.DRIVER_LABELS[sig]
        assert clean and fired
    for check in ("po_lookup", "line_reconciliation", "duplicate"):
        assert api_client.CHECK_LABELS[check]

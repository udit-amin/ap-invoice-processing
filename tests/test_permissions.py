"""Route permission matrix (PR1 subset: the existing routes).

These exercise the guards in isolation with synthetic tokens — no DB or API key
needed. For *allowed* roles we assert the request gets *past* the guard (i.e. not
401/403); the resulting 422 (missing file) or 404/503 (no such invoice / no DB)
all confirm authorization succeeded. Denials assert the exact 401/403.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


# --------------------------------------------------------------------------- #
# POST /process and /extract — clerk only
# --------------------------------------------------------------------------- #
def test_process_requires_authentication(client):
    assert client.post("/process").status_code == 401


def test_process_forbidden_for_manager(client, auth_header):
    assert client.post("/process", headers=auth_header("manager")).status_code == 403


def test_process_allowed_for_clerk(client, auth_header):
    # Clerk clears the guard; missing file → 422 (never 401/403).
    assert client.post("/process", headers=auth_header("clerk")).status_code not in (401, 403)


def test_extract_forbidden_for_manager(client, auth_header):
    assert client.post("/extract", headers=auth_header("manager")).status_code == 403


# --------------------------------------------------------------------------- #
# GET /audit/{invoice_number} — manager only
# --------------------------------------------------------------------------- #
def test_audit_requires_authentication(client):
    assert client.get("/audit/INV-1").status_code == 401


def test_audit_forbidden_for_clerk(client, auth_header):
    assert client.get("/audit/INV-1", headers=auth_header("clerk")).status_code == 403


def test_audit_allowed_for_manager(client, auth_header):
    # Manager clears the guard; 404 (no invoice) or 503 (no DB) — never 401/403.
    assert client.get("/audit/INV-1", headers=auth_header("manager")).status_code not in (401, 403)

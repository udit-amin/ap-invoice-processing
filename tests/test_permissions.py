"""Route permission matrix (full surface after PR2).

These exercise the guards in isolation with synthetic tokens — no DB or API key
needed. For *allowed* roles we assert the request gets *past* the guard (i.e. not
401/403); the resulting 422 (missing file/body) or 404/503 (no such row / no DB)
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
# Clerk-only: POST /invoices/process and POST /extract
# --------------------------------------------------------------------------- #
def test_process_requires_authentication(client):
    assert client.post("/invoices/process").status_code == 401


def test_process_forbidden_for_manager(client, auth_header):
    assert client.post("/invoices/process", headers=auth_header("manager")).status_code == 403


def test_process_allowed_for_clerk(client, auth_header):
    # Clerk clears the guard; missing file → 422 (never 401/403).
    assert client.post("/invoices/process", headers=auth_header("clerk")).status_code not in (401, 403)


def test_extract_forbidden_for_manager(client, auth_header):
    assert client.post("/extract", headers=auth_header("manager")).status_code == 403


# --------------------------------------------------------------------------- #
# Either role: GET /invoices/runs[/{id}], GET /review/queue, POST /review/.../action
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("role", ["clerk", "manager"])
def test_runs_allowed_for_both_roles(client, auth_header, role):
    assert client.get("/invoices/runs", headers=auth_header(role)).status_code not in (401, 403)


def test_runs_requires_authentication(client):
    assert client.get("/invoices/runs").status_code == 401


@pytest.mark.parametrize("role", ["clerk", "manager"])
def test_review_queue_allowed_for_both_roles(client, auth_header, role):
    assert client.get("/review/queue", headers=auth_header(role)).status_code not in (401, 403)


def test_review_queue_requires_authentication(client):
    assert client.get("/review/queue").status_code == 401


def test_review_action_requires_authentication(client):
    assert client.post("/review/some-run/action", json={"action": "approve"}).status_code == 401


# --------------------------------------------------------------------------- #
# Manager-only: GET /audit, GET /dashboard/*, GET/PUT /policy
# --------------------------------------------------------------------------- #
def test_audit_requires_authentication(client):
    assert client.get("/audit/INV-1").status_code == 401


def test_audit_forbidden_for_clerk(client, auth_header):
    assert client.get("/audit/INV-1", headers=auth_header("clerk")).status_code == 403


def test_audit_allowed_for_manager(client, auth_header):
    assert client.get("/audit/INV-1", headers=auth_header("manager")).status_code not in (401, 403)


@pytest.mark.parametrize("path", ["/dashboard/summary", "/dashboard/trends", "/policy"])
def test_manager_routes_forbidden_for_clerk(client, auth_header, path):
    assert client.get(path, headers=auth_header("clerk")).status_code == 403


@pytest.mark.parametrize("path", ["/dashboard/summary", "/dashboard/trends", "/policy"])
def test_manager_routes_require_authentication(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", ["/dashboard/summary", "/dashboard/trends", "/policy"])
def test_manager_routes_allowed_for_manager(client, auth_header, path):
    assert client.get(path, headers=auth_header("manager")).status_code not in (401, 403)


def test_put_policy_forbidden_for_clerk(client, auth_header):
    r = client.put("/policy", headers=auth_header("clerk"), json={"auto_approve_ceiling": 100000})
    assert r.status_code == 403

"""Thin wrapper over the v3 REST API — the ONLY module that knows the backend
URL and attaches the bearer token. One function per endpoint; every page calls
through here.

It also owns the non-technical *translation layer* (handover §3, §4.3, §7, §10):
internal stage/check/driver names → human labels. That mapping is presentation,
not business logic — no verdicts, tolerances, or confidence are computed here.
"""
from __future__ import annotations

import os
from typing import Any

import requests

import session

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/")
_TIMEOUT = 30
_PROCESS_TIMEOUT = 120  # extraction can take a beat


class ApiError(Exception):
    """A non-2xx response, carrying the status and the API's plain-English detail."""

    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(f"{status}: {detail}")

    def friendly(self) -> str:
        if self.status == 401:
            return "Your session expired — please log in again."
        if self.status == 403:
            return "You don't have access to this action."
        if self.status == 409:
            return self.detail  # over-commit message is already user-facing
        return self.detail or f"Request failed ({self.status})."


# --------------------------------------------------------------------------- #
# HTTP plumbing
# --------------------------------------------------------------------------- #
def _headers(auth: bool = True) -> dict[str, str]:
    h: dict[str, str] = {}
    if auth and session.token():
        h["Authorization"] = f"Bearer {session.token()}"
    return h


def _detail(resp: requests.Response) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict) and "detail" in body:
            d = body["detail"]
            return d if isinstance(d, str) else str(d)
    except ValueError:
        pass
    return resp.text or f"HTTP {resp.status_code}"


def _send(method: str, url: str, **kwargs) -> requests.Response:
    """Issue a request, turning transport failures (API down, DNS, timeout) into
    a friendly ApiError instead of a raw traceback in the UI."""
    try:
        return requests.request(method, url, **kwargs)
    except requests.RequestException as exc:
        raise ApiError(503, f"Can't reach the API at {API_BASE_URL} — is it running?") from exc


def _json(resp: requests.Response) -> Any:
    if resp.status_code >= 400:
        raise ApiError(resp.status_code, _detail(resp))
    return resp.json()


# --------------------------------------------------------------------------- #
# Endpoints (one per route)
# --------------------------------------------------------------------------- #
def login(email: str, password: str) -> dict[str, Any]:
    """POST /auth/login → {access_token, token_type, expires_in, user{name,role}}."""
    r = _send("post",f"{API_BASE_URL}/auth/login",
                      json={"email": email, "password": password}, timeout=_TIMEOUT)
    return _json(r)


def process_invoice(filename: str, data: bytes,
                    content_type: str = "application/pdf") -> dict[str, Any]:
    """POST /invoices/process → {run_id, extraction, validation, decision, events}."""
    files = {"file": (filename, data, content_type)}
    r = _send("post",f"{API_BASE_URL}/invoices/process",
                      headers=_headers(), files=files, timeout=_PROCESS_TIMEOUT)
    return _json(r)


def get_runs(verdict: str | None = None, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if verdict:
        params["verdict"] = verdict
    r = _send("get",f"{API_BASE_URL}/invoices/runs",
                     headers=_headers(), params=params, timeout=_TIMEOUT)
    return _json(r)


def get_run(run_id: str) -> dict[str, Any]:
    r = _send("get",f"{API_BASE_URL}/invoices/runs/{run_id}",
                     headers=_headers(), timeout=_TIMEOUT)
    return _json(r)


def get_review_queue() -> dict[str, Any]:
    r = _send("get",f"{API_BASE_URL}/review/queue", headers=_headers(), timeout=_TIMEOUT)
    return _json(r)


def get_review_detail(run_id: str) -> dict[str, Any]:
    r = _send("get",f"{API_BASE_URL}/review/{run_id}", headers=_headers(), timeout=_TIMEOUT)
    return _json(r)


def get_review_file(run_id: str) -> bytes | None:
    """GET /review/{run_id}/file → raw PDF bytes, or None if none stored (404)."""
    r = _send("get",f"{API_BASE_URL}/review/{run_id}/file",
                     headers=_headers(), timeout=_TIMEOUT)
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        raise ApiError(r.status_code, _detail(r))
    return r.content


def get_review_preview(run_id: str, page: int = 0) -> bytes | None:
    """GET /review/{run_id}/preview → a rendered PNG of one source page, or None."""
    r = _send("get",f"{API_BASE_URL}/review/{run_id}/preview",
                     headers=_headers(), params={"page": page}, timeout=_TIMEOUT)
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        raise ApiError(r.status_code, _detail(r))
    return r.content


def post_review_action(run_id: str, action: str, note: str | None) -> dict[str, Any]:
    r = _send("post",f"{API_BASE_URL}/review/{run_id}/action",
                      headers=_headers(), json={"action": action, "note": note},
                      timeout=_TIMEOUT)
    return _json(r)


def get_kpis(days: int = 30) -> dict[str, Any]:
    r = _send("get",f"{API_BASE_URL}/dashboard/kpis",
                     headers=_headers(), params={"days": days}, timeout=_TIMEOUT)
    return _json(r)


def get_trends(days: int = 30) -> dict[str, Any]:
    r = _send("get",f"{API_BASE_URL}/dashboard/trends",
                     headers=_headers(), params={"days": days}, timeout=_TIMEOUT)
    return _json(r)


def get_policy() -> dict[str, Any]:
    r = _send("get",f"{API_BASE_URL}/policy", headers=_headers(), timeout=_TIMEOUT)
    return _json(r)


def put_policy(changes: dict[str, Any]) -> dict[str, Any]:
    r = _send("put",f"{API_BASE_URL}/policy", headers=_headers(),
                     json=changes, timeout=_TIMEOUT)
    return _json(r)


def get_audit(invoice_number: str) -> dict[str, Any]:
    r = _send("get",f"{API_BASE_URL}/audit/{invoice_number}",
                     headers=_headers(), timeout=_TIMEOUT)
    return _json(r)


def reset_demo() -> dict[str, Any]:
    """POST /admin/reset-demo (manager) → re-seed back-dated demo history."""
    r = _send("post",f"{API_BASE_URL}/admin/reset-demo",
                      headers=_headers(), timeout=_PROCESS_TIMEOUT)
    return _json(r)


# --------------------------------------------------------------------------- #
# Translation layer — internal names → labels for the non-technical buyer.
# Kept here (one place) per handover §10.
# --------------------------------------------------------------------------- #

# The seven run-view stages, in order. Each resolves from the backend's
# governance events (handover §4.3).
STAGE_SEQUENCE = [
    ("received",  "Received"),
    ("read",      "Read invoice"),
    ("po",        "Found the PO"),
    ("vendor",    "Checked the vendor"),
    ("validated", "Validated amounts & lines"),
    ("decided",   "Made a decision"),
    ("logged",    "Logged"),
]

# Human labels for the seven checks ("What the system saw" panel).
CHECK_LABELS = {
    "po_lookup":           "PO match",
    "vendor_approved":     "Vendor approval",
    "po_status":           "PO status",
    "total_tolerance":     "Amount tolerance",
    "line_reconciliation": "Line items",
    "tax_present":         "Tax present",
    "duplicate":           "Duplicate check",
}

# Decision-card driver phrasing: signal → (clean label, fired label).
DRIVER_LABELS = {
    "po_lookup":           ("PO matched",                 "PO not found"),
    "vendor_approved":     ("Vendor approved",            "Vendor not approved"),
    "po_status":           ("PO open for billing",        "PO not open for billing"),
    "total_tolerance":     ("Amount within tolerance",    "Amount outside PO tolerance"),
    "line_reconciliation": ("Line items match the PO",    "Line items don't reconcile"),
    "tax_present":         ("Tax included on invoice",    "No tax on the invoice"),
    "duplicate":           ("Not a duplicate",            "Already processed (duplicate)"),
    "low_confidence":      ("Extraction confident",       "Low extraction confidence"),
    "incomplete":          ("All required fields present", "Required fields missing"),
    "over_authority":      ("Within approval authority",  "Over the auto-approve ceiling"),
    "balance_at_commit":   ("PO balance sufficient",      "PO balance insufficient at commit"),
}

# Review-queue bucket → a short "what the human is deciding" framing (§5.2).
QUEUE_LABELS = {
    "line_variance":  "Line variance — confirm the substitution",
    "tolerance":      "Amount over tolerance — confirm the overage",
    "over_authority": "Over authority ceiling — exercise spending authority",
    "missing_tax":    "No tax on the invoice — confirm tax-exempt or request a corrected invoice",
    "low_confidence": "Low confidence — verify the read against the source",
    "incomplete":     "Incomplete — complete the missing fields",
}

_DONE = {"ok": "done", "warn": "done", "fail": "fail", "error": "fail", "skip": "skipped"}


def _check_status(by_check: dict[str, str], name: str) -> str:
    return _DONE.get(by_check.get(name), "pending")


def stages_from_events(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Map the backend's governance events onto the seven display stages.

    Honest replay: every status comes from a real event the backend emitted; we
    only relabel and group them for a human watching. Returns
    [{key, label, status}], status ∈ pending|done|fail|skipped.
    """
    by_check: dict[str, str] = {}        # check name → event status
    by_stage: dict[str, list[str]] = {}  # coarse stage → statuses
    for e in events or []:
        stage = e.get("stage")
        status = e.get("status")
        by_stage.setdefault(stage, []).append(status)
        check = (e.get("detail") or {}).get("check")
        if check:
            by_check[check] = status

    def coarse(stage: str) -> str:
        statuses = by_stage.get(stage)
        if not statuses:
            return "pending"
        if any(s in ("fail", "error") for s in statuses):
            return "fail"
        if all(s == "skip" for s in statuses):
            return "skipped"
        return "done"

    has_events = bool(events)
    decided = bool(by_stage.get("decision"))
    resolved = {
        "received":  "done" if has_events else "pending",
        "read":      coarse("extract"),
        "po":        _check_status(by_check, "po_lookup"),
        "vendor":    _check_status(by_check, "vendor_approved"),
        "validated": coarse("validate"),
        "decided":   coarse("decision"),
        "logged":    "done" if decided else "pending",
    }
    return [{"key": key, "label": label, "status": resolved[key]}
            for key, label in STAGE_SEQUENCE]

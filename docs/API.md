# API Reference

Detailed usage for every endpoint: method, path, required role, request shape,
response shape, status codes, and a runnable `curl`. Base URL in examples is
`http://localhost:8000` (`uvicorn app.main:app --reload`). Interactive docs are
at `/docs` (Swagger) and `/redoc`.

For a quick overview see the table in [README](../README.md#running-the-api); for
the data those endpoints write, see [QUERYING_THE_DB.md](QUERYING_THE_DB.md).

---

## Authentication

All routes except `POST /auth/login` and `GET /health` require a bearer token:

```
Authorization: Bearer <access_token>
```

- **401 Unauthorized** — missing, malformed, expired, or invalid token.
- **403 Forbidden** — valid token, wrong role for the route.

Two roles: **clerk** (process invoices, review) and **manager** (review,
dashboard, policy, audit). Tokens are HS256 JWTs, 1-hour expiry by default
(`JWT_EXPIRE_SECONDS`).

**Demo users** (seeded): clerks `priya@acmecorp.com` / `rahul@acmecorp.com`
(`demo-clerk-1` / `demo-clerk-2`); managers `anjali@acmecorp.com` /
`vikram@acmecorp.com` (`demo-mgr-1` / `demo-mgr-2`).

### `POST /auth/login` · public

Request (JSON):

```json
{ "email": "priya@acmecorp.com", "password": "demo-clerk-1" }
```

Response `200`:

```json
{
  "access_token": "eyJhbGc…",
  "token_type": "bearer",
  "expires_in": 3600,
  "user": { "name": "Priya Nair", "role": "clerk" }
}
```

`401` on bad credentials.

```bash
curl -s -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"priya@acmecorp.com","password":"demo-clerk-1"}'
```

A handy shell helper used throughout this doc:

```bash
login() { curl -s -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$1\",\"password\":\"$2\"}" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])"; }

CLERK=$(login priya@acmecorp.com demo-clerk-1)
MGR=$(login anjali@acmecorp.com demo-mgr-1)
```

### `GET /auth/me` · any authenticated user

Response `200`: `{ "user_id", "email", "role", "name" }`.

```bash
curl -s http://localhost:8000/auth/me -H "Authorization: Bearer $CLERK"
```

---

## Meta

### `GET /health` · public

Liveness probe (used by the ALB target group). `200`:

```json
{ "status": "ok", "model": "claude-sonnet-4-6", "api_key_set": true }
```

```bash
curl -s http://localhost:8000/health
```

---

## Processing (clerk)

### `POST /extract` · clerk

Multipart upload of a PDF → structured extraction JSON only (no validation or
verdict). Calls the live model, so needs `ANTHROPIC_API_KEY`; without it the
response is a structured `error` payload (still HTTP 200).

- **422** — non-PDF or empty file.
- **403** — manager token. **401** — no token.

```bash
curl -s -X POST http://localhost:8000/extract \
  -H "Authorization: Bearer $CLERK" \
  -F "file=@data/inputs/normal_1_dell.pdf" | python3 -m json.tool
```

See [Extraction output schema](../README.md#extraction-output-schema-always-this-shape-missing-values-are-null).

### `POST /invoices/process` · clerk

*(Renamed from `POST /process` in v3.2.)* Multipart upload → the full pipeline.
The run is stamped with the acting clerk as its actor.

Response `200`:

```json
{
  "run_id": "239d4553-…",
  "extraction": { "...": "extraction schema" },
  "validation": { "...": "evidence report" },
  "decision": {
    "verdict": "FLAG",
    "reason": "Flagged for review: the amount exceeds the auto-approve ceiling …",
    "requires_human_review": true,
    "review_payload": { "queue": "over_authority", "what_to_check": "…" },
    "po_balance_after": null,
    "policy_version": "2026.06.1",
    "decided_at": "2026-06-10T14:00:00+00:00"
  },
  "events": [
    { "stage": "ingest",   "status": "ok",   "actor": "Priya Nair", "ts": "…" },
    { "stage": "extract",  "status": "ok",   "detail": { "source_type": "text" }, "ts": "…" },
    { "stage": "match",    "status": "ok",   "detail": { "check": "po_lookup" }, "ts": "…" },
    { "stage": "validate", "status": "fail", "detail": { "check": "line_reconciliation" }, "ts": "…" },
    { "stage": "decision", "status": "warn", "ts": "…" }
  ]
}
```

`po_balance_after` is non-null only on APPROVE (the PO was drawn down).
Re-processing the same invoice returns **REJECT (duplicate)** by design. The
`events` array is the run's ordered governance trail (added in v4) so a UI can
replay the real stages in a single call; the original PDF and full extraction
are also persisted (see `GET /review/{run_id}/file`).

```bash
curl -s -X POST http://localhost:8000/invoices/process \
  -H "Authorization: Bearer $CLERK" \
  -F "file=@data/inputs/edge_2_techgear_bundled.pdf" | python3 -m json.tool
```

---

## Runs (clerk → own, manager → all)

### `GET /invoices/runs` · clerk · manager

List processed runs, newest first. Clerks see only their own runs; managers see
all runs in the tenant.

Query params: `verdict` (`APPROVE|FLAG|REJECT`), `limit` (1–200, default 50),
`offset` (default 0). Invalid `verdict` → **422**.

Response `200`:

```json
{
  "runs": [
    {
      "run_id": "239d4553-…",
      "invoice_number": "TGEAR-2026-114",
      "vendor_name": "TechGear …",
      "po_reference": "PO-5009",
      "verdict": "FLAG",
      "requires_human_review": true,
      "invoice_total": 802400.0,
      "overall_conf": 0.95,
      "po_balance_after": null,
      "actor_user_id": "…",
      "actor_role": "clerk",
      "started_at": "…", "finished_at": "…"
    }
  ],
  "count": 1, "limit": 50, "offset": 0
}
```

```bash
curl -s "http://localhost:8000/invoices/runs?verdict=FLAG" \
  -H "Authorization: Bearer $CLERK" | python3 -m json.tool
```

### `GET /invoices/runs/{run_id}` · clerk → own · manager → any

One run's full detail: the run, its latest verdict, the validation report
summary, and the ordered governance events (each with actor fields). **404** if
the run doesn't exist or a clerk requests another user's run.

```bash
curl -s http://localhost:8000/invoices/runs/$RUN_ID \
  -H "Authorization: Bearer $MGR" | python3 -m json.tool
```

---

## Review (clerk · manager)

### `GET /review/queue` · clerk · manager

Flagged runs (`requires_human_review`) with no terminal (approve/reject) action
yet. An `escalate` leaves an item in the queue.

Response `200`:

```json
{
  "queue": [
    {
      "run_id": "239d4553-…",
      "invoice_number": "TGEAR-2026-114",
      "po_reference": "PO-5009", "vendor_name": "TechGear …",
      "verdict": "FLAG",
      "reason": "Flagged for review: the amount exceeds …",
      "review_payload": { "queue": "over_authority", "what_to_check": "…" },
      "invoice_total": 802400.0,
      "matched_po_id": "PO-5009",
      "decided_at": "…"
    }
  ],
  "count": 1
}
```

```bash
curl -s http://localhost:8000/review/queue -H "Authorization: Bearer $MGR" | python3 -m json.tool
```

### `POST /review/{run_id}/action` · clerk · manager

Apply a human decision. Request (JSON):

```json
{ "action": "approve", "note": "confirmed bundle pricing" }
```

- `action` ∈ `approve | reject | escalate` (other values → **422**).
- **`approve` is effectful**: it draws the matched PO down via the same race-safe
  `SELECT … FOR UPDATE` path the auto-decision uses. If the PO can no longer
  cover the invoice, the approval is refused with **409** and the item stays
  flagged (nothing is written). `reject` and `escalate` are record-only.
- Every action writes a `review_actions` row and a governance event
  (`review_approve | review_reject | review_escalate`) stamped with the actor.
- **404** if the run has no verdict.

Response `200`:

```json
{
  "review_id": 1,
  "run_id": "239d4553-…",
  "invoice_number": "TGEAR-2026-114",
  "po_reference": "PO-5009",
  "action": "approve",
  "po_balance_after": 0.0,
  "created_at": "…"
}
```

```bash
curl -s -X POST "http://localhost:8000/review/$RUN_ID/action" \
  -H "Authorization: Bearer $MGR" -H 'Content-Type: application/json' \
  -d '{"action":"approve","note":"confirmed bundle pricing"}' | python3 -m json.tool
```

### `GET /review/{run_id}` · clerk · manager

Full context for working a flagged run (the queue is global, so either role may
open any item — unlike the owner-scoped `/invoices/runs/{run_id}`). Powers the
three flag-type review views. **404** if the run has no verdict.

Returns the verdict (`verdict`, `reason`, `drivers`, `review_payload`,
`confidence_overall`, `policy_version`, `invoice_total`,
`auto_approve_ceiling_applied`), the persisted `extraction` (fields + per-field
confidence), the six `checks`, the per-line `line_reconciliation` side-by-side,
and `has_file`.

```bash
curl -s http://localhost:8000/review/$RUN_ID -H "Authorization: Bearer $MGR" | python3 -m json.tool
```

### `GET /review/{run_id}/file` · clerk · manager

Streams the original uploaded PDF (`application/pdf`) so the low-confidence view
can show the source scan. **404** if no file was stored for the run.

```bash
curl -s http://localhost:8000/review/$RUN_ID/file -H "Authorization: Bearer $MGR" -o invoice.pdf
```

### `GET /review/{run_id}/preview` · clerk · manager

Renders one page of the stored source PDF to PNG (`?page=`, default 0) for an
inline preview that's more reliable than embedding the PDF. **404** if no file
was stored or the page is out of range / unrenderable.

```bash
curl -s "http://localhost:8000/review/$RUN_ID/preview?page=0" \
  -H "Authorization: Bearer $MGR" -o page1.png
```

---

## Dashboard (manager — 403 for clerk)

### `GET /dashboard/summary` · manager

```json
{
  "verdicts": { "APPROVE": 6, "FLAG": 3, "REJECT": 2 },
  "needs_review": 3,
  "total_runs": 11,
  "as_of": "2026-06-10T14:00:00+00:00"
}
```

`needs_review` counts flagged runs without a terminal action.

```bash
curl -s http://localhost:8000/dashboard/summary -H "Authorization: Bearer $MGR" | python3 -m json.tool
```

### `GET /dashboard/trends` · manager

Per-day verdict buckets over the last `days` days (query param `days`, 1–365,
default 30).

```json
{
  "days": 30,
  "trends": [
    { "date": "2026-06-10", "APPROVE": 6, "FLAG": 3, "REJECT": 2, "total": 11 }
  ]
}
```

```bash
curl -s "http://localhost:8000/dashboard/trends?days=7" -H "Authorization: Bearer $MGR" | python3 -m json.tool
```

### `GET /dashboard/kpis` · manager

Headline KPIs over the last `days` days (query param `days`, 1–365, default 30),
each with a delta vs the prior equal-length period (`null` where no prior data).
All computed server-side. Quality KPIs (false-approve / override) are
deliberately omitted — they need a downstream ground-truth signal — so the UI
shows an honest placeholder rather than a fabricated number.

```json
{
  "as_of": "2026-06-14T00:00:00+00:00",
  "window_days": 30,
  "totals": { "verdicts": 11, "approve": 6, "flag": 3, "reject": 2 },
  "kpis": {
    "stp_rate": { "value": 0.5454, "delta": null },
    "avg_cycle_ms": { "value": 4.6, "delta": null },
    "avg_time_in_queue_sec": { "value": 420.0, "delta": null },
    "touchless_savings": { "value": 4380.0, "delta": 4380.0 },
    "audit_completeness": { "value": 1.0, "delta": null }
  },
  "flags_by_reason": { "line_variance": 1, "over_authority": 1, "low_confidence": 1 },
  "rejections_by_reason": { "closed_po": 1, "unapproved_vendor": 1, "po_not_found": 1 },
  "costs": { "manual_cost_per_invoice": 900.0, "auto_cost_per_invoice": 170.0 }
}
```

```bash
curl -s http://localhost:8000/dashboard/kpis -H "Authorization: Bearer $MGR" | python3 -m json.tool
```

---

## Policy (manager — 403 for clerk)

### `GET /policy` · manager

```json
{
  "auto_approve_ceiling": 750000.0,
  "default_tolerance_pct": 5.0,
  "confidence_threshold": 0.75,
  "min_confidence": 0.75,
  "policy_version": "2026.06.1",
  "severity_overrides": {}
}
```

### `PUT /policy` · manager

Edit any subset of the editable fields; the change is validated, `policy_version`
is auto-bumped, and a `policy_change` governance event is recorded with the
actor. The decision engine reads policy fresh, so the change applies to the next
run with no redeploy.

Request (JSON, all fields optional):

```json
{
  "auto_approve_ceiling": 200000,
  "min_confidence": 0.8,
  "severity_overrides": { "total_tolerance": "REJECT" }
}
```

Validation (→ **400** on failure): `auto_approve_ceiling > 0`;
`0 ≤ min_confidence ≤ 1`; `severity_overrides` keys must be known signals and
values ∈ `APPROVE | FLAG | REJECT`. Returns the full updated policy row (`200`).

```bash
curl -s -X PUT http://localhost:8000/policy \
  -H "Authorization: Bearer $MGR" -H 'Content-Type: application/json' \
  -d '{"auto_approve_ceiling":200000}' | python3 -m json.tool
```

---

## Audit (manager — 403 for clerk)

### `GET /audit/{invoice_number}` · manager

Reconstruct the full append-only trail for an invoice across all its runs.
`{invoice_number}` may contain `/` (URL-encode as `%2F`). **404** if no trail
exists; **503** if the store is unreachable.

Response `200`:

```json
{
  "invoice_number": "TGEAR-2026-114",
  "runs": [
    {
      "run_id": "239d4553-…",
      "started_at": "…", "finished_at": "…",
      "events": [
        { "stage": "ingest",   "status": "ok",   "actor": "Priya Nair",
          "actor_user_id": "…", "actor_role": "clerk", "action_type": "pipeline_run", "ts": "…" },
        { "stage": "decision", "status": "warn", "actor": "Priya Nair",
          "actor_role": "clerk", "action_type": "pipeline_run", "ts": "…" },
        { "stage": "review",   "status": "ok",   "actor": "Anjali Mehta",
          "actor_role": "manager", "action_type": "review_approve", "ts": "…" }
      ]
    }
  ],
  "latest_report": { "...": "evidence report JSONB" },
  "latest_verdict": { "verdict": "FLAG", "reason": "…", "po_balance_after": null, "…": "…" }
}
```

```bash
curl -s "http://localhost:8000/audit/TGEAR-2026-114" -H "Authorization: Bearer $MGR" | python3 -m json.tool
```

> `python -m json.tool` escapes non-ASCII (`₹` → `₹`); that's a printer
> artifact, not the API. Use `jq` or `python -m json.tool --no-ensure-ascii` to
> see the rupee sign.

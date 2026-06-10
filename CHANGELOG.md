# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project ships in
labelled milestones (`v1`, `v2`, `v3.x`) rather than on a fixed release cadence;
each PR adds an entry.

## [v3.2] — Endpoints + audit actor identity

The working API surface a UI needs, and a trail that records *who did what*.

### Added
- **Actor identity on the governance trail** — `actor_user_id`, `actor_role`,
  and `action_type` (`pipeline_run | review_approve | review_reject |
  review_escalate | policy_change`) on `governance_events`; creator columns on
  `pipeline_runs`. The acting user is threaded from the routers through the
  orchestrator and `commit_decision`.
- **`GET /invoices/runs`** and **`GET /invoices/runs/{run_id}`** — list/detail of
  processed runs, scoped by role (clerk sees own, manager sees all), with a
  `?verdict=` filter.
- **`GET /review/queue`** and **`POST /review/{run_id}/action`** — the human
  review workflow. `approve` is **effectful**: it draws the matched PO down via
  the same race-safe `SELECT … FOR UPDATE` path the auto-decision uses (refusing
  to over-commit); `reject`/`escalate` are record-only. Backed by a new
  `review_actions` table.
- **`GET /dashboard/summary`** and **`GET /dashboard/trends`** — manager-only
  verdict mix, review backlog, and per-day trends.
- **`GET /policy`** and **`PUT /policy`** — manager-only live policy editing
  (`auto_approve_ceiling`, `min_confidence`, `severity_overrides`), validated,
  version-bumped, and audited as a `policy_change` event.
- `invoice_total` / `matched_po_id` columns on `verdicts` so the review-approve
  path can draw the PO down without re-extracting.
- Docs: `CHANGELOG.md`, `CLAUDE.md`, and `docs/API.md` (full per-endpoint
  reference). New test suites: `test_invoices`, `test_review`, `test_dashboard`,
  `test_policy`; `test_governance` asserts the actor trail.

### Changed
- **Renamed `POST /process` → `POST /invoices/process`** (clerk-only, stamps the
  acting clerk). `POST /extract` is unchanged.
- Extracted the race-safe PO draw-down into a shared `commit.draw_down_po` helper
  reused by both the auto-decision and the review-approve path.

## [v3.1] — User model + JWT auth + `src/ → app/` restructure

### Added
- JWT auth (PyJWT HS256 + passlib/bcrypt): `POST /auth/login`, `GET /auth/me`,
  `get_current_user` (401) and `require_role` (403) guards.
- `users` table + idempotent seed of four demo users (2 clerks, 2 managers).
- Route guards: `/extract` and `/process` clerk-only, `/audit` manager-only.
- `tenant_id` column on the operational tables (constant tenant for now — a
  multi-tenancy hook, so future scoping is a `WHERE` filter, not a migration).
- Self-bootstrapping app factory: lifespan applies the schema and seeds reference
  data + users on startup. `.env.example`; `tests/test_auth`, `test_permissions`.

### Changed
- Physically moved `src/ → app/` (history preserved via `git mv`); split the
  monolithic `api.py` into an `app/main.py` app factory + per-domain routers.

## [v3.0] — Decision engine

### Added
- Decision engine: evidence + extraction confidence + policy → an
  `APPROVE | FLAG | REJECT` verdict by severity precedence, with a deterministic,
  LLM-free reason and review payload. The `verdicts` table is the one place a
  verdict is written.
- **Data-driven policy** (`policy_config`): `auto_approve_ceiling`,
  `min_confidence`, and a `severity_overrides` map — change a verdict without a
  code change.
- **Race-safe PO balance draw-down** on APPROVE (`SELECT … FOR UPDATE`), with a
  commit-time downgrade to FLAG when a concurrent invoice has drawn the PO down.

## [v2] — Postgres + governance

### Added
- Full cutover to Postgres (psycopg3 + raw SQL `schema.sql`).
- Append-only governance audit trail at every stage (ingest → extract → match →
  validate → decision), reconstructable per invoice.
- Race-proof duplicate detection via a `UNIQUE(invoice_number, vendor_name)`
  ledger.
- `POST /process` (full pipeline) and `GET /audit/{invoice_number}`.

## [v1] — Match + Validate

### Added
- Six-check evidence pipeline: `po_lookup`, `vendor_approved`, `po_status`,
  `total_tolerance`, `line_reconciliation`, `duplicate`. Fuzzy vendor/line
  matching (rapidfuzz). Evidence only — no verdict yet.

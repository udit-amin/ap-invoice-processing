# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project ships in
labelled milestones (`v1`, `v2`, `v3.x`) rather than on a fixed release cadence;
each PR adds an entry.

## [v4] — Streamlit UI + dashboard KPIs

The operational surface: a thin Streamlit client over the v3 API (login, live run
view, review queue, manager dashboard, policy editor) plus the small backend
additions it needs. No business logic in the UI — it calls endpoints and renders.

### Added
- **`ui/` Streamlit app** — login gate + role-driven `st.navigation` (clerks see
  Run view + Batch ingest + Review queue; managers see Review queue + Dashboard +
  Policy). One `api_client` wraps every endpoint and owns the human-label
  translation layer; `session` keeps the token across reruns. The run view
  replays the real governance events as a live stage tracker; **Batch ingest**
  runs every PDF in a server-side folder through the pipeline (progress + results
  table); the review queue renders a distinct view per flag type (line-variance
  side-by-side, over-ceiling amount, low-confidence scan + flagged fields, the
  scan rendered server-side to an image); the dashboard shows five KPI cards (+ an
  honest quality placeholder), flag/rejection breakdowns, a trend chart, and a
  runs table with an audit drill-in.
- **`GET /dashboard/kpis`** (manager) — STP rate, avg cycle time, avg time-in-
  queue, touchless savings, audit completeness, and flags/rejections-by-reason,
  each with a prior-period delta, in one payload. Quality KPIs (false-approve /
  override) are deliberately omitted, not faked; duplicate detection is a
  *safeguard*, surfaced only in the rejections breakdown — not a savings KPI.
- **`GET /review/{run_id}`**, **`/file`**, and **`/preview`** — full review
  context (drivers, review payload, extraction, per-line side-by-side), the
  stored original PDF, and a server-rendered PNG of a source page for an inline
  preview. Either role (the queue is global).
- `manual_cost_per_invoice` / `auto_cost_per_invoice` on `policy_config`
  (₹900 / ₹170) for touchless savings; `pipeline_runs.extraction` JSONB + an
  `invoice_files` (BYTEA) table persisting each upload, so the review UI can show
  extracted fields and the source scan after the fact.
- `scripts/seed_demo_history.py` — back-dated runs (with stored files/extraction)
  so the dashboard isn't empty in a demo. New tests: `test_pipeline_events`,
  `test_ui_labels`, plus KPI / review-detail / runs-amount coverage.

### Changed
- **`POST /invoices/process` now returns the `events` array** (the run's ordered
  governance trail) so the UI replays the real stages in a single call.
- **`GET /invoices/runs`** list items now include `invoice_total` and
  `overall_conf` for the dashboard runs table.
- The deterministic decision path is untouched — verdicts stay reproducible
  (the 11-fixture matrix is still 6 APPROVE / 3 FLAG / 2 REJECT).

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

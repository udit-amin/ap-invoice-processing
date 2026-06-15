# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project ships in
labelled milestones (`v1`, `v2`, `v3.x`) rather than on a fixed release cadence;
each PR adds an entry.

## [v4.6] — Review workflow: two-tier queue, manager overrides, reviewer identity

### Changed
- **Review queue is role-scoped.** A clerk works first-line flags; **Escalate** routes an
  item to the **manager's** queue (a manager's queue is the escalated items). Managers are
  the final tier (no escalate button).
- **Processed tab shows only *settled* invoices** — a flagged invoice appears here once a
  human has reviewed it (`GET /invoices/runs?settled=true`); pending/escalated flags stay
  in the queue.
- **Managers can flip a settled verdict** (APPROVE ↔ REJECT) from Processed, with a
  required note; clerks are view-only.
- **Reviewer identity + note surfaced.** A review action records the reviewer's **email**
  (`review_actions.actor_email`); the Processed detail shows who reviewed it, when, and
  their note (`review_detail.latest_action`; runs list carries `last_action_by`/`_note`).

## [v4.5] — Batch ingest, demo reset, and a docs cleanup

### Added
- **Batch ingest via multi-file upload** (`ui/views/batch_ingest.py`) — drag several
  PDFs → each through `process_invoice` → a mixed-verdict results table. Works on the
  deployment (no S3/filesystem dependency); a server-folder mode stays for local/worker
  parity. The worker now sweeps `landing` recursively so a date-partitioned
  `landing/<YYYYMMDD>/` works, mirroring the archive partition.
- **"Reset demo data" button** (manager, Dashboard → Demo controls) backed by
  `POST /admin/reset-demo` (`app/admin/`) — clears all processed runs and restores POs to
  baseline (a clean slate). **Gated by `ALLOW_DEMO_RESET`** (on for the demo Render
  services, off in real prod) and manager-only. `scripts/seed_demo_history.py` is a thin
  wrapper over the shared `app.admin.service.reset_demo_data`. New `tests/test_admin.py`.
- **Curated demo data** — `scripts/make_demo_invoices.py` emits a straight-through
  `data/demo/batch/` (3 APPROVE + 2 REJECT) and `data/demo/edges/` (the edge cases to walk
  through one at a time: over-ceiling, missing-tax, line-variance → FLAG; a scanned invoice
  → APPROVE via the vision path). The demo starts on a clean slate and builds up live.

### Changed
- **Docs trimmed to four** (`docs/`): Operations (now also covers deploy + CI/CD),
  Usage (how to work the UI + the demo walkthrough), API, and Architecture (rewritten
  Render-centric). Removed the demo/deployment/manual-testing/db-querying docs; README
  slimmed to an overview.

## [v4.4] — Tax-presence check (7th validation check)

Adds a tax control: an invoice must declare tax, otherwise it's flagged.

### Added
- **`tax_present` check** (`app/validate/checks.py`) — a **pure presence check** on the
  extractor's `tax.treatment`: `separated`/`embedded` → pass, `none` → **fail (FLAG)**,
  null/unknown → **skip**. It does no amount arithmetic and never touches the PO, so it
  can't conflict with `total_tolerance` (tax-inclusive totals) or `line_reconciliation`
  (ex-tax line prices) — those keep owning the amount math. Default severity FLAG
  (`policy.py`), data-driven/overridable; wired through validator → engine → reason
  (new review queue `missing_tax`) and the UI labels.
- A reserved **ex-tax `PO-5011`** + a `demo_flag_notax.pdf` generator case
  (`scripts/make_live_demo_invoices.py`) so the missing-tax flag demos cleanly (it's the
  *only* failing check). New tax tests in `test_validation` + `test_decision`.

### Notes
- **Skip-on-unknown** is deliberate: the answer key carries no tax (`treatment=null`), so
  `validate_all --dry-run`, CI's smoke, and `seed_demo_history` stay at **6 APPROVE /
  3 FLAG / 2 REJECT** with no answer-key changes. Live extraction always classifies tax,
  so a genuine no-tax invoice still flags.
- Docs bumped "six/6 checks" → "seven/7" across README, CLAUDE, ARCHITECTURE, API, DEMO.

## [v4.3] — CI/CD + staging/production gitflow

Test-gated continuous delivery on top of the Render deploy. No application code
changes — pipelines + branch strategy + docs.

### Added
- **CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) — on every PR and
  push to `main`: a Postgres service container, schema + reference + user seeding,
  the full `pytest` suite, the `validate_all --dry-run` verdict-matrix smoke, and a
  Docker build of the deploy image. Runs on Python 3.12 (matches the image), with no
  `ANTHROPIC_API_KEY` so the live-model tests skip and CI stays free/deterministic.
  Exposed as a reusable workflow (`workflow_call`).
- **CD** ([`.github/workflows/deploy.yml`](.github/workflows/deploy.yml)) — on a merge
  into `staging` / `production` (or manual dispatch): re-runs CI, then POSTs the
  matching Render **deploy hook(s)**. Skips gracefully (a warning, never a failure)
  when a hook secret is absent, so it's inert until configured.
- **`feature → develop → staging → production`** branch strategy, documented in
  [docs/OPERATIONS.md](docs/OPERATIONS.md) (secrets, per-env Render services, disabling
  Render auto-deploy so CI gates, and branch protection). `render.yaml` provisions
  **both** environments (staging + production service sets, branch-pinned,
  `autoDeploy: false`) in one Blueprint.

### Fixed
- Deploy docs now seed demo history **from a laptop via the database's External URL**
  (free-tier Render has no Shell/SSH) — corrected in README, OPERATIONS, and DEMO.

## [v4.2] — Live deployment (Render) + demo kit

Makes the process *live and runnable* at a public URL and prepares the demo. No
application code changes — the app already reads every knob from the environment
and self-bootstraps; this round is deploy artifacts + demo tooling + docs.

### Added
- **One-image container** — a repo-root [`Dockerfile`](Dockerfile) (+ `.dockerignore`)
  that runs both roles: the API (`uvicorn`, default CMD) and the UI (`streamlit`,
  command overridden). Realises the "one image, multiple entrypoints" story.
- **Render Blueprint** — [`render.yaml`](render.yaml): a managed Postgres plus two
  web services (`ap-api`, `ap-ui`). UI → API is server-side, so there's no CORS;
  the grader only needs the `ap-ui` URL. The API self-applies schema + seeds on boot.
- **Live-demo invoice generator** — `scripts/make_live_demo_invoices.py` mints
  fresh-numbered PDFs (Dell→APPROVE, Globex→REJECT, TechGear→FLAG) into
  `data/demo_live/`, so the live happy-path upload is a clean APPROVE rather than a
  duplicate. Verdicts verified end-to-end through the real pipeline.
- **A demo guide** — operational-flow one-pager, a timed 5-minute video script (happy
  path + edge cases + manager + close), and a live runbook (later folded into
  [docs/USAGE.md](docs/USAGE.md)).

### Changed
- `README.md` (a "Live deployment (Render)" section) and `docs/OPERATIONS.md` §3 (the
  concrete Render path beside the AWS outline) document the deploy.

## [v4.1] — Folder ingestion, decision monitoring, AWS docs

A demo-readiness round on top of v4.

### Added
- **Ingestion worker** (`app/ingest/worker.py`; `python -m app.ingest.worker`) —
  sweeps a *landing* folder, runs each PDF through the same `process_invoice`
  pipeline (stamped as the system actor), and moves it to an *archive* folder
  partitioned by processing date (`YYYYMMDD`). Simulates the AWS S3 landing →
  archive worker; a file that fails to process is left in landing for retry.
  `--seed` copies the demo fixtures into landing.
- **Processed view** (`ui/views/decisions.py`; clerk + manager) — monitor every
  AI decision (clerk sees own, manager sees all) and **manually reject
  (override)** one, recorded on the governance trail. `GET /invoices/runs` now
  returns `last_action`, so a human override shows beside the AI verdict.
- **AWS docs** — `docs/ARCHITECTURE.md` (pretend-AWS topology + flows + design
  trade-offs) and `docs/OPERATIONS.md` (deploy, config, worker, monitoring,
  runbooks, demo script).

### Changed
- Demo users rebranded from `@acmecorp.com` to **`@zamp.ai`**.
- **Source-document preview now renders the native PDF** (`st.pdf` /
  `streamlit-pdf`) for *every* invoice — selectable text for text invoices (e.g.
  DEL/2026/0419), the page image for scans — in the run view, the review detail
  (all flag types, not just low-confidence), and the Processed view. Supersedes
  the rasterised-image preview for the UI; `requirements` gains `streamlit[pdf]`.

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

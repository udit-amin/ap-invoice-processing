# AP Invoice Processing

An auditable accounts-payable pipeline: a supplier invoice PDF goes in, and out
comes structured extraction data, per-check validation evidence, **and a
reasoned verdict** (`APPROVE | FLAG | REJECT`) — with an append-only governance
trail recording every step. It handles machine-readable and image-only (scanned)
PDFs, itemised and bundled lines, separated and embedded tax, fuzzy vendor/line
matching, race-proof duplicate detection, and a race-safe PO balance update on
approval.

The pipeline has four stages:

```
            ┌─ extract ─┐  ┌─ match ─┐  ┌─ validate ─┐  ┌─ decide ──┐
 PDF ─ingest┤ Claude →  │→ │ PO +    │→ │ 7 checks → │→ │ policy →   │→ verdict
            │ JSON      │  │ vendor  │  │ evidence   │  │ APPROVE/   │  + reason
            └───────────┘  └─────────┘  │(no verdict)│  │ FLAG/REJECT│
                                        └────────────┘  └───────────┘
        every stage emits an append-only governance event (Postgres)
```

**Separation of concerns:** validation *gathers facts*; the decision engine
*applies policy* to those facts. The validator never mentions a verdict; the
engine never re-derives facts — it reads the evidence, the extraction
confidence, and `policy_config`, and is the **only** place a verdict is written.
The decision path is deterministic and LLM-free, so verdicts are auditable and
reproducible.

## What's here

```
app/
  main.py                 FastAPI app factory + lifespan (migrate→seed→serve), /health
  config.py               Model string, thresholds, DB DSN, JWT + tenant settings, paths
  auth/
    router.py             POST /auth/login, GET /auth/me
    service.py            bcrypt hashing, JWT issue/verify, authenticate
    dependencies.py       get_current_user + require_role guards
  users/
    models.py             Raw-SQL user-row helpers
    seed.py               Idempotent seed of the four demo users
  pipeline/
    orchestrator.py       ingest → extract → match → validate → decide (stamps actor)
    router.py             POST /extract, POST /invoices/process (clerk-only)
  invoices/
    router.py             GET /invoices/runs, GET /invoices/runs/{run_id}
    models.py             Tenant-scoped run list/detail (clerk = own, manager = all)
  review/
    router.py             GET /review/queue + /{run_id} (+ /file, /preview), POST /review/{run_id}/action
    service.py            Effectful approve (PO draw-down) / reject / escalate; review-detail read
  dashboard/
    router.py             GET /dashboard/summary, /trends, /kpis (manager-only)
    models.py             Verdict-mix + daily-trend aggregates + headline KPIs
  policy/
    router.py             GET /policy, PUT /policy (manager-only)
    service.py            Validate + version-bump + audit policy edits
  audit/
    router.py             GET /audit/{invoice_number} (manager-only)
  db/
    schema.sql            Postgres tables (incl. users + tenant_id columns)
    connection.py         psycopg connection pool + schema apply
    seed.py               Seed vendors, POs + line items, policy_config
  extract/
    ingest.py             Detect text vs image-only PDF
    text_extract.py       pdfplumber path
    vision_extract.py     PyMuPDF → base64 path
    prompts.py            Cached system instructions for Claude
    extractor.py          Extraction orchestrator — PDF in, JSON out
  validate/
    loader.py             Load POs + vendors from Postgres
    matcher.py            Fuzzy vendor / line-item matching (rapidfuzz)
    checks.py             The seven validation checks
    validator.py          Runs checks → evidence report (no verdict)
  decide/
    policy.py             Load policy_config + data-driven severity map
    engine.py             Pure resolver: evidence + confidence + policy → verdict
    reason.py             Deterministic reason + review-payload assembly
    commit.py             Race-safe PO balance update + verdict persistence
  governance/
    recorder.py           Append-only audit trail (runs, events, reports) + actor identity
  ingest/
    worker.py             Landing → process → archive (YYYYMMDD); simulates the AWS pickup worker
  generate/
    invoice_generator.py     v0 synthetic PDFs (extraction tests)
    invoice_generator_v1.py  v1 synthetic PDFs (validation tests)
ui/                       Streamlit front end (v4) — thin client over the API
  app.py                  Login gate + role-driven st.navigation + sidebar
  api_client.py           One fn per endpoint + bearer + human-label translation
  session.py              Token + current user in st.session_state
  views/                  run_view, batch_ingest, decisions, review_queue, dashboard, policy (one render() each)
  components/             decision_card, stage_tracker, invoice_detail
validate_all.py           CLI harness — runs all 11 invoices, prints the matrix + verdicts
scripts/
  seed_demo_history.py    Back-dated runs (+ stored files) so the dashboard isn't empty
docker-compose.yml        Local Postgres
.env.example              Documented environment variables
CHANGELOG.md              Version history (v1 → v4)
CLAUDE.md                 Repo guide + invariants for contributors / AI agents
docs/
  API.md                  Detailed per-endpoint usage reference (curl + shapes)
  ARCHITECTURE.md         AWS deployment architecture (topology, flows, trade-offs)
  OPERATIONS.md           Deploy, configure, run the worker, monitor, runbooks
  MANUAL_TESTING.md       Step-by-step manual test guide
  QUERYING_THE_DB.md      How to inspect the database
tests/
  conftest.py             Shared fixtures (auth headers)
  test_extraction.py      Extraction schema + behaviour (live, needs API key)
  test_validation.py      Pure validation logic (no infra)
  test_governance.py      Postgres + governance + actor trail (skip-if-down)
  test_decision.py        Decision engine — pure matrix + DB commit (skip-if-down)
  test_auth.py            JWT issue/verify + login/me (login skips if DB down)
  test_permissions.py     Route role-guard matrix (no infra)
  test_invoices.py        Run list/detail scoping (skip-if-down)
  test_review.py          Review queue + effectful approve/reject/escalate (skip-if-down)
  test_dashboard.py       Dashboard aggregates + KPIs + clerk 403 (skip-if-down)
  test_pipeline_events.py /invoices/process events + file/extraction persistence (skip-if-down)
  test_ui_labels.py       UI translation layer (stage mapping + label maps; no infra)
  test_ingest.py          Ingest worker date partitioning + landing→archive sweep (no infra)
  test_policy.py          Live policy edit flips next verdict (skip-if-down)
```

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Provide an Anthropic API key (read from the environment or an untracked `.env`
file that is gitignored):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# or:  echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env
```

Start Postgres, apply the schema + seed reference data, and generate the test
invoices:

```bash
docker compose up -d                        # local Postgres
python -m app.db.seed                        # schema + vendors, POs, policy
python -m app.users.seed                     # four demo users (2 clerks, 2 managers)
python -m app.generate.invoice_generator     # v0 PDFs (extraction tests)
python -m app.generate.invoice_generator_v1  # v1 PDFs (validation tests)
```

The API server also self-bootstraps on startup (apply schema → seed reference
data → seed users), so `uvicorn app.main:app` works against an empty database
with no extra steps; the commands above are for the CLI/test workflows.

Override the database with `DATABASE_URL` (env or `.env`); it defaults to
`postgresql://ap:ap@localhost:5432/ap_invoices`. Copy **`.env.example`** to
`.env` for the full list of variables — set `JWT_SECRET` (required when
`ENVIRONMENT=production`; a dev fallback is used otherwise).

> New here? See **[docs/MANUAL_TESTING.md](docs/MANUAL_TESTING.md)** for a
> guided walkthrough and **[docs/QUERYING_THE_DB.md](docs/QUERYING_THE_DB.md)**
> to inspect what the pipeline writes.

## Running the API

```bash
uvicorn app.main:app --reload
```

| Endpoint | Purpose | Role |
|----------|---------|------|
| `POST /auth/login` | Email + password → bearer JWT (1-hour expiry) | public |
| `GET  /auth/me` | Current user (UI session bootstrap) | any |
| `GET  /health` | Liveness + whether the API key is set | public |
| `POST /extract` | PDF → structured extraction JSON only | clerk |
| `POST /invoices/process` | PDF → full pipeline: extraction + validation + **verdict** | clerk |
| `GET  /invoices/runs` | List processed runs (clerk → own, manager → all; `?verdict=`) | clerk · manager |
| `GET  /invoices/runs/{run_id}` | One run's detail (run + verdict + events) | clerk · manager |
| `GET  /review/queue` | Flagged runs awaiting a human decision | clerk · manager |
| `GET  /review/{run_id}` | Flagged-run review context (drivers, fields, line side-by-side) | clerk · manager |
| `GET  /review/{run_id}/file` | The original uploaded PDF (for the low-confidence scan view) | clerk · manager |
| `GET  /review/{run_id}/preview` | A rendered PNG of a source page (inline preview) | clerk · manager |
| `POST /review/{run_id}/action` | `approve` (draws PO down) / `reject` / `escalate` | clerk · manager |
| `GET  /dashboard/summary` | Verdict mix, review backlog, totals | manager |
| `GET  /dashboard/trends` | Per-day verdict counts (`?days=`) | manager |
| `GET  /dashboard/kpis` | Headline KPIs + flag/rejection breakdowns (`?days=`) | manager |
| `GET  /policy` | Current governance policy | manager |
| `PUT  /policy` | Edit ceiling / confidence / severity map (audited) | manager |
| `GET  /audit/{invoice_number}` | Governance trail + latest verdict (incl. actor) | manager |

> See **[docs/API.md](docs/API.md)** for the full per-endpoint reference —
> request/response shapes, status codes, and a runnable `curl` for each.

### Authentication & roles

Two roles: **clerk** (uploads/processes invoices, reviews) and **manager**
(reviews, dashboard, policy, audit). Protected routes require an `Authorization:
Bearer <token>` header; a clerk hitting a manager route (or vice-versa) gets
**403**, a missing/invalid token gets **401**. Four demo users are seeded —
clerks `priya@zamp.ai` / `rahul@zamp.ai` (`demo-clerk-1` / `-2`),
managers `anjali@zamp.ai` / `vikram@zamp.ai` (`demo-mgr-1` / `-2`).

```bash
# 1) clerk logs in and processes an invoice → run is stamped with the clerk
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"priya@zamp.ai","password":"demo-clerk-1"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

RUN=$(curl -s -X POST http://localhost:8000/invoices/process \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@data/inputs/edge_2_techgear_bundled.pdf" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['run_id'])")

# 2) manager reviews the flagged run and approves it (draws the PO down)
MGR=$(curl -s -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"anjali@zamp.ai","password":"demo-mgr-1"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -s http://localhost:8000/review/queue -H "Authorization: Bearer $MGR" | python3 -m json.tool
curl -s -X POST "http://localhost:8000/review/$RUN/action" \
  -H "Authorization: Bearer $MGR" -H 'Content-Type: application/json' \
  -d '{"action":"approve","note":"confirmed bundle pricing"}' | python3 -m json.tool

# 3) manager dashboard + audit trail (the trail now shows who did what)
curl -s http://localhost:8000/dashboard/summary -H "Authorization: Bearer $MGR" | python3 -m json.tool
```

`/invoices/process` returns `{run_id, extraction, validation, decision, events}`.
Every run and review action is recorded to the append-only governance trail in
Postgres (`pipeline_runs`, `governance_events`, `validation_reports`, `verdicts`,
`review_actions`), each stamped with the acting user (`actor_user_id`,
`actor_role`, `action_type`). Audit writes are best-effort — a logging failure
never breaks the response.

## The UI (v4)

A Streamlit front end in `ui/` — a **thin client** over the API (it calls
endpoints and renders; no verdict/tolerance logic lives in the UI). Run it
alongside the API:

```bash
# API on :8000 (see above), then in another shell:
API_BASE_URL=http://localhost:8000 .venv/bin/streamlit run ui/app.py
# optional: pre-load a few days of history so the dashboard isn't empty
.venv/bin/python scripts/seed_demo_history.py
```

`API_BASE_URL` (default `http://localhost:8000`) is the only deployment knob —
point it at the AWS backend to run the same UI against production. Log in as any
seeded user; the sidebar shows only the pages your role can use:

| Page | clerk | manager | What it does |
|------|:---:|:---:|------|
| **Run view** | ✅ | — | Upload a PDF → the seven stages light up from the **real** governance events → decision card + "what the system saw" |
| **Batch ingest** | ✅ | — | Upload several PDFs at once (or point at a server-side folder) → each through the pipeline → progress + a results table. The deployment's batch entry (prod = an S3 landing→archive worker, date-partitioned) |
| **Review queue** | ✅ | ✅ | Flagged items (oldest-first) with a distinct view per flag type: line-variance side-by-side, over-ceiling amount, low-confidence scan + flagged fields, missing-tax → approve / reject / escalate |
| **Processed** | ✅ | ✅ | Every AI decision (clerk → own, manager → all) — monitor it or **manually reject (override)** |
| **Dashboard** | — | ✅ | Five KPI cards (+ an honest quality-monitoring placeholder), flag/rejection breakdowns, a 30-day trend, a filterable runs table with an audit drill-in, and **Demo controls → Reset demo data** (gated by `ALLOW_DEMO_RESET`) |
| **Policy** | — | ✅ | Edit the auto-approve ceiling / confidence gate live (a subsequent fresh run reflects it) |

The run view's stage replay paces the **real** events the backend emitted (the
pipeline itself runs in well under a second) — no timings or statuses are
invented. Quality KPIs (false-approve / override) are shown as an honest
"coming soon" placeholder rather than fabricated, since they need a downstream
ground-truth signal.

## Automated ingestion & deployment

An ingestion worker simulates the AWS pickup flow — invoices land in one place,
get processed, and are archived by date:

```bash
.venv/bin/python -m app.ingest.worker --seed
# demo fixtures → data/landing/ → pipeline → data/archive/<YYYYMMDD>/
```

It sweeps a *landing* folder, runs each PDF through the **same** pipeline as the
UI (stamped as the system actor), and moves it to a date-partitioned *archive*
folder; a file that fails to process is left for retry. For how this maps to AWS
(ALB · ECS Fargate · RDS · S3 landing/archive · scheduled worker · Secrets
Manager · Bedrock/Anthropic · CloudWatch) and how to operate it, see
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** and
**[docs/OPERATIONS.md](docs/OPERATIONS.md)**.

### Live deployment (Render)

One [`render.yaml`](render.yaml) Blueprint provisions **two environments** from a
single [`Dockerfile`](Dockerfile) — staging (`ap-api-staging` + `ap-ui-staging`,
branch `staging`) and production (`ap-api-prod` + `ap-ui-prod`, branch
`production`), each with its own Postgres. The browser only talks to the Streamlit
URL; the UI calls the API server-side, so there's **no CORS** to configure. The API
self-applies the schema and seeds reference data + demo users on first boot.

```text
browser → ap-ui-<env> (Streamlit, public) → ap-api-<env> (FastAPI) → ap-invoices-db-<env>
```

Each service is `autoDeploy: false`; deploys are driven by the **CI-gated** GitHub
deploy hooks (a merge into `staging`/`production` runs CI, then deploys that env).
**Full step-by-step setup — apply the Blueprint, env vars, hooks, secrets — is in
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).**

**The grader opens the `ap-ui-prod` URL.** Logins: `priya@zamp.ai` / `demo-clerk-1`
(clerk), `anjali@zamp.ai` / `demo-mgr-1` (manager). The 5-minute video script and
the live runbook (warm the URL, reset state, which PDFs to upload) are in
**[docs/DEMO.md](docs/DEMO.md)**.

### CI/CD (GitHub Actions)

A `feature → develop → staging → production` gitflow with test-gated deploys
(`develop` is the repo's default/integration branch):

- **CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs on every PR
  and push to `develop` — a Postgres service, the full `pytest` suite, the
  `validate_all --dry-run` verdict-matrix smoke, and a Docker build of the deploy image.
- **CD** ([`.github/workflows/deploy.yml`](.github/workflows/deploy.yml)) runs on a
  merge into `staging` or `production`: it re-runs CI and, only if green, POSTs the
  matching Render **deploy hook** — so nothing reaches production without passing tests.

Setup (secrets, Render environments, branch protection) is in
**[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.

## The validation checks

The validator runs seven checks; each returns `pass | fail | skip` with a
human-readable reason. `skip` is a first-class outcome (the check could not run
meaningfully by design) — it is never collapsed into pass or fail.

| Check | Question it answers |
|-------|---------------------|
| `po_lookup` | Does the cited PO exist? (miss → short-circuits checks 3–5 to skip) |
| `vendor_approved` | Does the vendor fuzzy-match an **approved** registry entry? |
| `po_status` | Is the matched PO `open`? |
| `total_tolerance` | Is the invoice total within the PO's own `tolerance_pct`? |
| `line_reconciliation` | Do line qty/price reconcile? (bundled / embedded-tax fallbacks) |
| `tax_present` | Does the invoice declare tax (separated or embedded)? `none` → FLAG; unknown → skip |
| `duplicate` | First time we've seen this invoice? (race-proof via a DB unique constraint) |

`tax_present` is a **pure presence check** on the extractor's tax classification —
it does no amount math and never touches the PO, so it can't conflict with
`total_tolerance` / `line_reconciliation` (which already handle the tax-inclusive
total vs ex-tax line prices).

`line_reconciliation` classifies each line as `exact_match` / `price_variance` /
`qty_variance` / `qty_and_price_variance` / `unmatched_invoice_line` /
`uninvoiced_po_line`, and **skips** on a bundled invoice or an embedded-tax
invoice whose rate it cannot back out.

The headline case is `edge_4`: the total matches the PO exactly (tolerance
passes) but the lines were re-quoted — line reconciliation catches what total
matching misses.

## Pipeline behaviour worth knowing

- **Bundled lines** — a single line at one combined price with no per-component
  breakdown is emitted as `is_bundle: true` with `unit_price: null`; prices are
  never invented, and line reconciliation skips rather than failing.
- **Embedded tax** — tax-inclusive prices with no tax line are detected and the
  implied per-line ex-tax is derived (`incl / (1 + rate)`) before reconciling;
  if the rate can't be recovered, the check skips and validates at total level.
- **Fuzzy matching** — vendor names tolerate legal suffixes ("India Pvt Ltd"),
  line descriptions tolerate abbreviations ("hrs" ≈ "hours"). Thresholds: 0.75
  vendor, 0.80 line (`app/validate/matcher.py`).
- **Tolerance lives in data** — each PO carries its own `tolerance_pct`; nothing
  is hard-coded.

## Validation report schema (evidence only — never a verdict)

```json
{
  "invoice_number": "DEL/2026/0419",
  "po_reference": "PO-5001",
  "matched_po": "PO-5001",
  "checks": [
    {"check": "po_lookup",         "status": "pass", "reason": "PO-5001 found in database"},
    {"check": "vendor_approved",   "status": "pass", "reason": "'Dell …' is approved (V-001)"},
    {"check": "po_status",         "status": "pass", "reason": "PO PO-5001 is open"},
    {"check": "total_tolerance",   "status": "pass", "reason": "within 0.0% of PO balance (allowed 3%)"},
    {"check": "line_reconciliation","status": "fail", "reason": "2 of 2 line(s) mismatch",
       "detail": [ { "invoice_line": "Latitude 5440 Laptop", "matched_po_line": "Latitude 5440 Laptop",
                     "classification": "qty_and_price_variance",
                     "invoice": {"qty": 7, "unit_price": 60000}, "po": {"qty": 5, "unit_price": 72000} } ]},
    {"check": "duplicate",         "status": "pass", "reason": "First occurrence of invoice DEL/2026/0419"}
  ],
  "summary": {"passed": 5, "failed": 1, "skipped": 0},
  "events": [ {"stage": "match", "status": "ok", "ts": "…"}, {"stage": "validate", "status": "ok", "ts": "…"} ]
}
```

## The decision engine

The engine turns evidence into one verdict by **severity precedence** —
`REJECT > FLAG > APPROVE` — taking the most severe contribution across all
signals. A single hard failure rejects; anything uncertain, over-authority, or
anomalous lands in `FLAG` (the safety valve) rather than a wrong auto-approve.

| Signal | Contributes |
|--------|-------------|
| `po_lookup` / `vendor_approved` / `po_status` / `duplicate` fail | **REJECT** |
| `total_tolerance` / `line_reconciliation` fail | **FLAG** |
| extraction `overall` confidence < `min_confidence` | **FLAG** (low_confidence) |
| any required field missing | **FLAG** (incomplete) |
| invoice total > `auto_approve_ceiling` | **FLAG** (over_authority) |
| `line_reconciliation` skip (bundled / embedded-tax) | no contribution; noted in reason |
| everything clean | **APPROVE** |

The map is **data-driven**: `policy_config.severity_overrides` (JSONB) remaps any
signal's severity without a code change. Governance defaults (in `policy_config`,
stamped onto every verdict as `policy_version`):

```
auto_approve_ceiling = 750000      # INR; total above → FLAG (authority)
min_confidence       = 0.75        # extraction overall below → FLAG
policy_version       = "2026.06.1"
```

On **APPROVE only**, the engine decrements the PO `remaining_balance` inside a
`SELECT … FOR UPDATE` transaction (closing the PO at zero). If a concurrent
invoice drew the balance down since validation, the approve is **downgraded to
FLAG** rather than over-committing the PO — verdict, balance change, and
governance event commit or roll back together. The decision path is
deterministic and LLM-free; reasons are reproducible byte-for-byte.

### Verdict object (the only place a verdict is written)

```json
{
  "invoice_number": "DEL/2026/0419",
  "po_reference": "PO-5001",
  "verdict": "FLAG",
  "reason": "Flagged for review: line items do not reconcile with the PO (2 of 2 line(s) mismatch). The invoice total matches the PO within tolerance, so a human should confirm the substitution.",
  "drivers": [
    {"signal": "line_reconciliation", "outcome": "fail", "severity": "FLAG", "detail": "2 of 2 line(s) mismatch"},
    {"signal": "total_tolerance", "outcome": "pass", "severity": "APPROVE", "detail": "within 0.0% of PO balance (allowed 3%)"}
  ],
  "requires_human_review": true,
  "review_payload": {"queue": "line_variance", "what_to_check": "Confirm the billed quantities and unit prices against the PO before payment.", "extracted_total": 566400, "po_balance": 566400},
  "confidence_overall": 0.95,
  "policy_version": "2026.06.1",
  "auto_approve_ceiling_applied": 750000,
  "po_balance_after": null,
  "decided_at": "2026-06-10T12:00:00Z"
}
```

`po_balance_after` is non-null only on APPROVE; `requires_human_review` is true
iff the verdict is FLAG.

## Extraction output schema (always this shape, missing values are `null`)

```json
{
  "source_type": "text | scanned",
  "invoice_number": "INV-2026-0042",
  "vendor_name": "Dell Technologies",
  "invoice_date": "2026-05-14",
  "po_reference": "PO-5001",
  "currency": "INR",
  "line_items": [
    {"description": "Laptop + headphone bundle", "quantity": 5, "unit_price": null,
     "line_total": 450000, "is_bundle": true, "bundle_components": ["Laptop", "Headphones"]}
  ],
  "subtotal": 450000,
  "tax": { "amount": 81000, "rate_pct": 18, "treatment": "separated" },
  "total": 531000,
  "extraction_confidence": {
    "invoice_number": 0.98, "vendor_name": 0.96,
    "po_reference": 0.91, "total": 0.94, "overall": 0.90
  },
  "extraction_notes": ["Line 1 is bundled; could not separate unit prices."],
  "error": null
}
```

## The verdict matrix

Run the whole invoice set through the pipeline and print the check matrix + the
verdict column:

```bash
python validate_all.py --dry-run     # answer-key extraction, no LLM call
python validate_all.py               # live extraction (needs ANTHROPIC_API_KEY)
```

| Invoice | Verdict | Dominant driver |
|---------|---------|-----------------|
| normal_1…5 | **APPROVE** | clean, confident, under ceiling (decrements PO) |
| edge_1 (scanned) | **FLAG** | low_confidence |
| edge_2 (bundled, ₹8.02L) | **FLAG** | over_authority (line-recon skip noted) |
| edge_3 (embedded tax) | **APPROVE** | lines reconcile via derived ex-tax |
| edge_4 (line mismatch) | **FLAG** | line_reconciliation (total still matches) |
| edge_5 (Globex → PO-9999) | **REJECT** | po_lookup + vendor_approved |
| edge_6 (closed PO-5003) | **REJECT** | po_status |
| any invoice re-run | **REJECT** | duplicate |

Three distinct FLAG reasons (confidence, authority, line variance) and two
distinct REJECT reasons (not-found/fraud, closed PO). The matrix is per-invoice
in isolation — the harness reseeds PO balances before each invoice so one
approval's decrement doesn't change another's verdict. By default it also resets
operational state for reproducibility; `--keep` accumulates (watch `duplicate`
flip to REJECT on a second run).

Verdicts are data-driven — lower the ceiling and the normals flip with no code
change:

```bash
docker exec ap_invoices_db psql -U ap -d ap_invoices \
  -c "UPDATE policy_config SET auto_approve_ceiling=200000 WHERE id=1;"
python validate_all.py --dry-run     # normals now FLAG (over_authority)
```

## Tests

```bash
pytest                               # everything (skips DB/live tests when unavailable)
pytest tests/test_validation.py -v   # pure validation logic, no infra
pytest tests/test_permissions.py -v  # route role-guard matrix, no infra
pytest tests/test_auth.py -v         # JWT (pure) + login/me (skips if DB down)
pytest tests/test_decision.py -v     # decision matrix (pure) + commit (skips if DB down)
pytest tests/test_review.py -v       # review queue + effectful actions (skips if DB down)
pytest tests/test_policy.py -v       # live policy edit flips verdict (skips if DB down)
pytest tests/test_governance.py -v   # Postgres + actor trail (skips if DB down)
pytest tests/test_extraction.py -v   # extraction (live-model tests skip w/o key)
```

`test_validation.py`, `test_permissions.py`, and the pure halves of
`test_decision.py` / `test_auth.py` always run (in-memory data + synthetic
tokens). The DB-backed tests (`test_invoices`, `test_review`, `test_dashboard`,
`test_policy`, `test_governance`) skip cleanly when Postgres is unreachable.
Live-model extraction tests skip when `ANTHROPIC_API_KEY` is unset.

## Stack

Python 3.11+ · FastAPI + uvicorn · **JWT auth (PyJWT + passlib/bcrypt, role-based
guards)** · anthropic SDK (Sonnet, prompt caching) · **Postgres + psycopg3** ·
rapidfuzz · pdfplumber · PyMuPDF + Pillow · reportlab · pytest + httpx

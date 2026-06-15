# Operations guide

Deploying, running, monitoring, and troubleshooting the AP invoice processor —
locally and on Render (it scales to AWS unchanged). For *how it's built*, see
[ARCHITECTURE.md](ARCHITECTURE.md); for using the app, [USAGE.md](USAGE.md); for
endpoints, [API.md](API.md).

---

## 1. What you operate

Four runtime pieces, all from one container image:

| Piece | Entrypoint | Notes |
|---|---|---|
| **API** | `uvicorn app.main:app` | Stateless; self-applies schema + seeds on startup |
| **UI** | `streamlit run ui/app.py` | Thin client; needs `API_BASE_URL` |
| **Ingest worker** | `python -m app.ingest.worker` | Scheduled/triggered; landing → archive |
| **Database** | RDS PostgreSQL | The one stateful store |

## 2. Configuration

Set via environment / Secrets Manager (never commit secrets). See `.env.example`.

| Variable | Purpose | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude extraction | Secret. Without it, extraction is skipped/falls back |
| `DATABASE_URL` | Postgres DSN | Defaults to the local compose DSN |
| `JWT_SECRET` | HS256 signing key | **Required** when `ENVIRONMENT=production` (dev fallback refused) |
| `JWT_EXPIRE_SECONDS` | Token lifetime | Default 3600 |
| `ENVIRONMENT` | `production` \| `development` | Prod hardens the JWT secret check |
| `API_BASE_URL` | UI → API base URL | UI only; point at the ALB/internal API |

Governance knobs are **data**, not env: `policy_config` (auto-approve ceiling,
confidence gate, per-PO tolerance, severity map, manual/auto per-invoice costs).
Edit live via `PUT /policy` or the Policy page — no redeploy.

## 3. Deploy & CI/CD

### Branch strategy

`feature/* → develop → staging → production`. `develop` is the integration branch
(every PR runs CI; merging does not deploy). A PR merged into `staging` or
`production` deploys **that** environment. Promote by PR — each hop re-runs the
suite, so nothing reaches production untested.

### Pipelines (GitHub Actions)

- **CI** (`.github/workflows/ci.yml`) — every PR and push to `develop`: a Postgres
  service, the full `pytest` suite, the `validate_all --dry-run` verdict-matrix
  smoke, and a Docker build of the deploy image. Reusable via `workflow_call`.
- **CD** (`.github/workflows/deploy.yml`) — on a merge into `staging`/`production`
  (or manual dispatch): re-runs CI, then — only if green — POSTs that environment's
  Render **deploy hook**. Skips with a warning (never fails) if a hook secret is
  unset, so it's inert until configured.

### Render (the live deployment)

One image, driven by `render.yaml` (Blueprint) at the repo root. One apply
provisions **two environments**: staging (`ap-api-staging`, `ap-ui-staging`,
`ap-invoices-db-staging`, branch `staging`) and production (`ap-api-prod`,
`ap-ui-prod`, `ap-invoices-db-prod`, branch `production`). UI → API is server-side,
so there's no CORS. Each service is `autoDeploy: false` — deploys come only from the
CI-gated GitHub hooks. First-time setup:

1. Render → **New → Blueprint** → pick the repo (reads `render.yaml` from `develop`).
2. Set `ANTHROPIC_API_KEY` on each `*-api` service; after the first deploy, set each
   `*-ui` service's `API_BASE_URL` to its sibling api URL (e.g.
   `https://ap-api-prod.onrender.com`).
3. Copy each service's **Deploy Hook** into the GitHub secrets
   `RENDER_DEPLOY_HOOK_{STAGING,PRODUCTION}_{API,UI}`.
4. Seed each env's back-dated demo data from your laptop against the db's **External
   URL** (free tier has no Shell): `DATABASE_URL='<external-url>?sslmode=require'
   .venv/bin/python scripts/seed_demo_history.py`. The same URL drives every
   reset/edit later (§6).

The grader opens the **`ap-ui-prod`** URL. Free-tier services sleep after ~15 min
idle (~30–60 s cold start) — warm the URLs before a demo, or use the Starter tier for
the grading window. The demo walkthrough is in [USAGE.md](USAGE.md).

> A new `render.yaml` env (e.g. `ALLOW_DEMO_RESET`) needs a Blueprint **sync** in
> Render to reach existing services, or set it on the service directly.

### Scales to AWS

The same image maps to ECS Fargate (API + UI services + a scheduled worker task)
behind an ALB, with RDS Multi-AZ, S3 landing/archive buckets, and Secrets Manager —
no code change. The API self-bootstraps the schema on first boot; seed reference data
+ users once (`python -m app.db.seed && python -m app.users.seed`).

### Local

```bash
docker compose up -d db
.venv/bin/python -m app.db.seed && .venv/bin/python -m app.users.seed
.venv/bin/python -m uvicorn app.main:app --reload
API_BASE_URL=http://localhost:8000 .venv/bin/streamlit run ui/app.py
.venv/bin/python scripts/seed_demo_history.py   # the two starting flagged runs
```

Demo logins: `priya@/rahul@zamp.ai` (clerk), `anjali@/vikram@zamp.ai` (manager);
passwords `demo-clerk-1/2`, `demo-mgr-1/2`.

## 4. The ingest worker (landing → archive) + batch on the deploy

Sweeps the landing area (recursively, so a date-partitioned
`landing/<YYYYMMDD>/` works), runs each PDF through the pipeline, and moves it to
`archive/<YYYYMMDD>/` under the same date. A file that fails to process **stays in
landing** for retry (route to a DLQ in production).

```bash
.venv/bin/python -m app.ingest.worker                 # data/landing → data/archive
.venv/bin/python -m app.ingest.worker --seed          # copy demo fixtures into landing first
.venv/bin/python -m app.ingest.worker --landing /mnt/landing --archive /mnt/archive
```

> **On the Render deploy there's no S3 and the disk is ephemeral**, so the worker's
> folders aren't a batch source there. The deployed **batch entry is the UI's Batch
> ingest page (multi-file upload)** — the same `process_invoice` per file. The S3
> landing→archive worker is the AWS production design (and the local simulation above).

On AWS: an EventBridge schedule starts this as an ECS task on an interval, or an
S3 `ObjectCreated` event triggers a per-object run. It calls the same pipeline as
the API (stamped as the **system** actor), so its results appear in the review
queue, the **Processed** view, and the dashboard. It needs `DATABASE_URL` +
`ANTHROPIC_API_KEY` and IAM to read landing / write archive.

## 5. Monitoring & health

- **Liveness:** `GET /health` → `{status, model, api_key_set}` (ALB target-group
  check). `api_key_set:false` means extraction will fail — check the secret.
- **Product health (dashboard / `GET /dashboard/kpis`):** watch **STP rate** (is
  automation working?), **flag rate by reason** (a rising *low-confidence* share
  = extraction struggling with new formats/scans; rising *over-ceiling* = the
  ceiling may be too low), **rejections by reason** (a *duplicate* spike = a
  vendor double-sending; *unapproved-vendor* spike = stale registry), **avg
  time-in-queue** (human bottleneck), **audit completeness** (should be ~100%).
- **CloudWatch:** container logs (API/UI/worker), ECS CPU/mem, ALB 5xx + target
  health, RDS connections/CPU/storage. Suggested alarms: ALB 5xx > threshold;
  `/health` failing; RDS connections near the pool ceiling; worker task failures;
  landing-prefix object age (files not being swept).

## 6. Routine operations

- **Reseed reference data / policy:** `python -m app.db.seed` (idempotent upsert).
- **Adjust policy live:** Policy page or `PUT /policy` (ceiling / confidence /
  severity). Reflected on the **next** run, no redeploy. Re-process a *fresh*
  invoice to see it (a re-run of a seen invoice returns REJECT-duplicate).
- **Manually override an AI decision:** **Processed** view → open the invoice →
  *Reject (override)*. Recorded on the trail with the actor. (Note: a manual
  reject *after* an auto-approve records the override but does **not** reverse a
  committed PO draw-down — issue a compensating credit out-of-band.)
- **Clean operational state (demo):** easiest is the manager's **Dashboard → ⚠️ Demo
  controls → Reset demo data** button (`POST /admin/reset-demo`), which truncates the
  operational tables, restores every PO to its baseline, and re-seeds the **two starting
  flagged runs**. It's **gated by `ALLOW_DEMO_RESET=true`** (set on the demo services
  only) and manager-only — leave the env unset in a real production so processed data
  can't be wiped by a click. Equivalent CLI: `python scripts/seed_demo_history.py`. Or raw:
  ```sql
  TRUNCATE review_actions, governance_events, validation_reports,
           verdicts, invoices, invoice_files, pipeline_runs RESTART IDENTITY CASCADE;
  ```
- **Back up / restore:** RDS automated snapshots + PITR; the trail is append-only,
  so point-in-time restore reconstructs full history. Test restores periodically.
- **Rotate secrets:** update in Secrets Manager → roll the ECS service. JWTs are
  short-lived (default 1h), so a `JWT_SECRET` rotation drains within the TTL.
- **Scale:** raise Fargate desired count (API/UI are stateless); keep the psycopg
  pool × task-count under the RDS `max_connections`.

## 7. Runbooks

- **UI shows "Can't reach the API":** the API/ALB is down or `API_BASE_URL` is
  wrong. Check `/health`, ECS task health, ALB target group, security groups.
- **DB unreachable:** DB-backed routes 5xx and `pytest` DB tests skip. Check RDS
  status/connections, the ECS task SG → RDS SG path, and `DATABASE_URL`. The app
  fails fast (5s pool timeout) rather than hanging.
- **Extraction failing / low confidence everywhere:** verify `ANTHROPIC_API_KEY`
  (`/health.api_key_set`), model availability/quota, and NAT egress (or Bedrock
  access). Scanned PDFs route to the vision path — expect lower confidence; the
  low-confidence flag is *by design*, sending them to human review.
- **Review-queue backlog (time-in-queue rising):** more humans, or (if flags are
  mostly *over-ceiling* and legitimate) raise the auto-approve ceiling — watching
  the false-approve risk.
- **Duplicate spike:** the `UNIQUE(invoice_number, vendor_name)` ledger is
  rejecting re-sends correctly; confirm the upstream isn't re-dropping the same
  files into landing.
- **Landing files not moving:** worker not running (check the schedule/task) or
  failing per-file (they stay in landing by design) — inspect worker logs; a real
  deployment drains repeated failures to a DLQ.
- **Suspected bad auto-approve:** open it in **Processed** → *Reject (override)*;
  consider lowering the ceiling or tightening the confidence gate via policy.

## 8. Demo data

The demo opens with **two flagged runs** already in the review queue; the batch and
edge uploads build the rest up live. The full clerk/manager walkthrough is in
[USAGE.md](USAGE.md). Operationally:

1. Generate the demo invoices: `python scripts/make_demo_invoices.py` → writes
   `data/demo/batch/` (3 APPROVE + 2 REJECT) and `data/demo/edges/` (over-ceiling FLAG
   + a scanned invoice that runs the vision path and approves).
2. Reset to the starting state any time: **Dashboard → Demo controls → Reset demo
   data** (or `python scripts/seed_demo_history.py`). This restores POs and re-seeds
   the two starting flags (line-variance + missing-tax), so the batch + edge uploads
   are repeatable.

> Re-uploading an already-seen invoice returns **REJECT (duplicate)** by design —
> reset (above) for a clean run, or use it deliberately to show the duplicate guard.

## 9. SLOs & known limitations

- **Targets (illustrative):** API p95 < 300 ms (non-extraction); extraction
  1–5 s/invoice (model-bound); auto-decision sub-second; audit completeness 100%.
- **Deliberately deferred:** false-approve and human-override **rate** KPIs need a
  downstream ground-truth signal — shown as an honest placeholder, not faked.
- **Manual reject after auto-approve** records the override but doesn't reverse a
  committed PO draw-down (compensate out-of-band).
- **Single tenant** today (constant `tenant_id`); the scoping is in place for
  multi-tenant but per-request tenant resolution isn't wired yet.

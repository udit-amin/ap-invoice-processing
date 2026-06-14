# Operations guide

Running, monitoring, and troubleshooting the AP invoice processor — locally and
on AWS. For *how it's built*, see [ARCHITECTURE.md](ARCHITECTURE.md); for
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

## 3. Deploy

**AWS (outline)** — build the image → push to ECR → ECS Fargate services (API,
UI) + an EventBridge-scheduled worker task; ALB in front; RDS Multi-AZ; landing
+ archive S3 buckets; secrets in Secrets Manager. The API self-bootstraps the
schema on startup, so first boot against an empty RDS just works; then seed
reference data + users once:

```bash
python -m app.db.seed       # vendors, POs + line items, policy_config (incl. costs)
python -m app.users.seed    # the demo users (replace with real users in prod)
```

**Render (the live demo deployment)** — the same image, driven by `render.yaml`
(Blueprint) at the repo root: a managed Postgres + two web services, `ap-api`
(`uvicorn`) and `ap-ui` (`streamlit`). UI → API is server-side, so there's no
CORS. Steps:

1. Render → **New → Blueprint** → pick the repo (creates DB + both services).
2. Set `ANTHROPIC_API_KEY` on `ap-api` (secret). After the first deploy, set
   `ap-ui`'s `API_BASE_URL` to `ap-api`'s public URL (e.g.
   `https://ap-api.onrender.com`) — Render's `fromService` gives only the bare
   host, and `api_client` needs the full `https://` URL — then redeploy `ap-ui`.
3. First API boot self-applies the schema + seeds reference data + the 4 users.
4. `ap-api` → **Shell** → `python scripts/seed_demo_history.py` (no key needed)
   for ~5 days of back-dated history so the dashboard isn't empty.

The grader opens the **`ap-ui`** URL. Free-tier services sleep after ~15 min idle
(~30–60 s cold start) — warm both URLs before a demo, or use the Starter tier for
the grading window. The video script + live runbook are in [DEMO.md](DEMO.md).

**Local / demo**

```bash
docker compose up -d db
.venv/bin/python -m app.db.seed && .venv/bin/python -m app.users.seed
.venv/bin/python -m uvicorn app.main:app --reload
API_BASE_URL=http://localhost:8000 .venv/bin/streamlit run ui/app.py
.venv/bin/python scripts/seed_demo_history.py   # back-dated history so the dashboard isn't empty
```

Demo logins: `priya@/rahul@zamp.ai` (clerk), `anjali@/vikram@zamp.ai` (manager);
passwords `demo-clerk-1/2`, `demo-mgr-1/2`.

## 4. The ingest worker (landing → archive)

Sweeps the landing area, runs each PDF through the pipeline, and moves it to
`archive/<YYYYMMDD>/`. A file that fails to process **stays in landing** for
retry (route to a DLQ in production).

```bash
.venv/bin/python -m app.ingest.worker                 # data/landing → data/archive
.venv/bin/python -m app.ingest.worker --seed          # copy demo fixtures into landing first
.venv/bin/python -m app.ingest.worker --landing /mnt/landing --archive /mnt/archive
```

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
- **Clean operational state (demo):** truncate the operational tables (keep
  reference data), then reseed history:
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

## 8. Demo runbook

1. Seed history: `python scripts/seed_demo_history.py` (5 days of back-dated runs).
2. **Clerk (Priya):** Run view → upload a fresh PDF → watch the seven stages
   replay → decision card. Then **Batch ingest** → point at `data/landing`
   (`--seed` the worker first, or `cp data/inputs/*.pdf data/landing/`).
3. **Automated path:** in a terminal, `python -m app.ingest.worker --seed` →
   show files moving from `data/landing/` to `data/archive/<YYYYMMDD>/`.
4. **Manager (Anjali):** Dashboard (KPIs, breakdowns, trend, runs table → audit
   trail with actor names); **Processed** → override an auto-approve; **Review
   queue** → work the three flag types; **Policy** → lower the ceiling, then
   process a fresh invoice to show the verdict flip.

> Tip: re-ingesting already-seen invoices returns **REJECT (duplicate)** by
> design. Truncate operational tables (§6) for a clean APPROVE/FLAG demo.

## 9. SLOs & known limitations

- **Targets (illustrative):** API p95 < 300 ms (non-extraction); extraction
  1–5 s/invoice (model-bound); auto-decision sub-second; audit completeness 100%.
- **Deliberately deferred:** false-approve and human-override **rate** KPIs need a
  downstream ground-truth signal — shown as an honest placeholder, not faked.
- **Manual reject after auto-approve** records the override but doesn't reverse a
  committed PO draw-down (compensate out-of-band).
- **Single tenant** today (constant `tenant_id`); the scoping is in place for
  multi-tenant but per-request tenant resolution isn't wired yet.

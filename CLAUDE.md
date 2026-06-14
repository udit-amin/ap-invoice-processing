# CLAUDE.md

Guidance for Claude Code (and humans) working in this repo. Keep this current
when architecture or invariants change. The user-facing overview is `README.md`;
the endpoint reference is `docs/API.md`; version history is `CHANGELOG.md`; AWS
deployment + ops live in `docs/ARCHITECTURE.md` / `docs/OPERATIONS.md`.

## What this is

An auditable accounts-payable pipeline: invoice PDF in → structured extraction →
7-check validation evidence → an `APPROVE | FLAG | REJECT` verdict, with an
append-only governance trail and role-based API on top.

## Architecture (the stage boundary matters)

```
PDF ─ingest→ extract ─→ match ─→ validate ─→ decide ─→ verdict (+ governance trail)
            (Claude)   (PO+vendor) (7 checks) (policy)
```

- `app/extract/` — PDF → JSON (Claude; text vs vision path auto-detected).
- `app/validate/` — `matcher.py` (fuzzy PO/vendor), `checks.py` (the 7 checks),
  `validator.py` → an **evidence report, never a verdict**.
- `app/decide/` — `policy.py` (loads `policy_config` + severity map),
  `engine.py` (**pure** resolver: evidence + confidence + policy → verdict),
  `reason.py` (deterministic reason/review payload), `commit.py` (persist verdict
  + race-safe PO draw-down).
- `app/governance/recorder.py` — append-only trail (runs, events, reports) +
  actor identity helpers.
- `app/pipeline/orchestrator.py` — `process_invoice(...)`; the single entry the
  API route, `validate_all.py`, **and** the ingest worker all call, so the trail
  is identical.
- `app/ingest/worker.py` — landing → `process_invoice` → archive partitioned by
  `YYYYMMDD` (same pipeline, stamped system; simulates the AWS S3 pickup worker).
- Routers: `app/pipeline` (`/extract`, `/invoices/process`), `app/invoices`
  (runs), `app/review` (queue + actions + `/{run_id}` detail + `/file` + `/preview`),
  `app/dashboard` (`/summary`, `/trends`, `/kpis`), `app/policy`, `app/audit`,
  `app/auth`. Wired in `app/main.py`.
- `ui/` — Streamlit **thin client** (v4) over the API: `api_client.py` (one fn
  per endpoint + the human-label translation layer), `session.py`, `app.py`
  (role-based `st.navigation`), `views/`, `components/`. `API_BASE_URL` (env) is
  the only backend knob. The run view replays the real `events` array returned by
  `/invoices/process`; the source PDF + per-field extraction are persisted
  (`invoice_files`, `pipeline_runs.extraction`) so the review queue can show them.

**Separation of concerns:** validation *gathers facts*; the engine *applies
policy*. The validator must never mention a verdict; the engine must never
re-derive facts. `verdicts` is the one place a verdict is written.

## Invariants — do not break these

1. **Best-effort governance.** `recorder.*` and audit writes never raise — a
   logging failure must not break the pipeline it observes. Reads
   (`fetch_audit_trail`) do raise.
2. **Race-safe money.** The PO draw-down goes through `commit.draw_down_po`
   (`SELECT … FOR UPDATE`, downgrade rather than over-commit). Both the
   auto-decision and the review-approve path use it — never duplicate the lock
   logic or decrement a balance elsewhere.
3. **Duplicate detection** rests on `UNIQUE(invoice_number, vendor_name)` in
   `invoices`. Don't weaken it.
4. **Policy is data, read fresh.** `load_policy()` reads `policy_config` on every
   decision; editing it (seed or `PUT /policy`) changes verdicts with no
   redeploy. Don't cache it across requests or hard-code thresholds in code.
5. **Decision path is deterministic + LLM-free.** Verdicts must be reproducible
   byte-for-byte. Only extraction calls the model.
6. **Tenant discipline.** Every new query filters by `config.TENANT_ID`; new
   operational rows carry `tenant_id` (constant for now).
7. **Actor on the trail.** User-initiated writes stamp `actor_user_id` /
   `actor_role` / `action_type`; thread the `CurrentUser` through, defaulting to
   the system actor (None) for the harness.
8. **No business logic in the UI.** `ui/` calls endpoints and renders — it never
   computes a verdict, tolerance, or confidence. New aggregations/KPIs are
   server-side (`dashboard/models.py`); the human-label mapping is the one bit of
   logic that lives in the UI (`api_client.py`), and it's pure presentation.

## Conventions (pragmatic-hybrid — deliberate)

- **Sync psycopg3 + raw SQL** (`app/db/schema.sql`), idempotent
  `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE … ADD COLUMN IF NOT EXISTS`.
  **No** SQLAlchemy / asyncpg / Alembic. New routes are sync FastAPI handlers
  (they run in the threadpool).
- Config is the lightweight `app/config.py` `_read_env_value` module — not
  Pydantic `BaseSettings`.
- New domain package = `__init__.py` + `router.py` + a `models.py` (reads) or
  `service.py` (writes/effectful), mirroring `app/users/`.
- Data dir stays `data/` (fixtures in `data/inputs/`).

## Run / seed / test

Use the project venv: `.venv/bin/python` (the base interpreter lacks deps).

```bash
docker compose up -d db                 # Postgres (DSN postgresql://ap:ap@localhost:5432/ap_invoices)
.venv/bin/python -m app.db.seed         # schema + vendors/POs/policy
.venv/bin/python -m app.users.seed      # 4 demo users
.venv/bin/python -m uvicorn app.main:app --reload   # API (self-bootstraps on startup too)
API_BASE_URL=http://localhost:8000 .venv/bin/streamlit run ui/app.py   # UI (v4)
.venv/bin/python -m app.ingest.worker --seed        # landing → process → archive/YYYYMMDD
.venv/bin/python scripts/seed_demo_history.py       # back-dated demo data (dashboard not empty)
.venv/bin/python -m pytest -q           # full suite
.venv/bin/python validate_all.py --dry-run          # 11-invoice matrix, no model calls
```

Demo users: `priya@/rahul@zamp.ai` (clerk, `demo-clerk-1/2`),
`anjali@/vikram@zamp.ai` (manager, `demo-mgr-1/2`).

## Tests

`pytest` skips DB-backed tests when Postgres is down and live-model tests when
`ANTHROPIC_API_KEY` is unset (`requires_db` / `requires_api` patterns) — so the
suite is always runnable. Pure tests (`test_validation`, `test_permissions`, the
pure halves of `test_decision`/`test_auth`) need no infra. Mint synthetic tokens
via `tests/conftest.py:auth_header` (role guards read the JWT, no DB needed).

## Gotchas

- Verdict matrix for the 11 fixtures: 6 APPROVE / 3 FLAG / 2 REJECT. If
  `validate_all.py --dry-run` deviates, the pipeline regressed.
- Re-processing a fixture returns **REJECT (duplicate)** — that's correct, not a
  bug. Truncate operational tables + reseed to get a clean APPROVE/FLAG.
- The API returns real UTF-8 (`₹`); `python -m json.tool` escapes it to `₹`
  — a display artifact, not a data bug. Use `jq` or `--no-ensure-ascii`.
- `match`/`validate` governance events are stamped as the system actor (they're
  deterministic checks); the run itself plus `ingest`/`extract`/`decision` carry
  the user.
- Commit messages end with the `Co-Authored-By: Claude …` trailer; branch per PR.

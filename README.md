# AP Invoice Processing

An auditable accounts-payable pipeline: a supplier invoice PDF goes in, and out comes
structured extraction, per-check validation evidence, **and a reasoned verdict**
(`APPROVE | FLAG | REJECT`) — with an append-only governance trail recording every step.
It handles machine-readable and scanned PDFs, itemised and bundled lines, separated and
embedded tax, fuzzy vendor/line matching, race-proof duplicate detection, and a race-safe
PO draw-down on approval.

**Live app:** https://ap-ui-prod.onrender.com/ — sign in as `priya@zamp.ai` /
`demo-clerk-1` (clerk) or `anjali@zamp.ai` / `demo-mgr-1` (manager).

```
            ┌─ extract ─┐  ┌─ match ─┐  ┌─ validate ─┐  ┌─ decide ──┐
 PDF ─ingest┤ Claude →  │→ │ PO +    │→ │ 7 checks → │→ │ policy →   │→ verdict
            │ JSON      │  │ vendor  │  │ evidence   │  │ APPROVE/   │  + reason
            └───────────┘  └─────────┘  │(no verdict)│  │ FLAG/REJECT│
                                        └────────────┘  └───────────┘
        every stage emits an append-only governance event (Postgres)
```

**Separation of concerns:** validation *gathers facts*; the decision engine *applies
policy*. The validator never mentions a verdict; the engine reads the evidence, the
extraction confidence, and `policy_config`, and is the only place a verdict is written.
The decision path is deterministic and LLM-free — only extraction calls the model — so
verdicts are reproducible and auditable.

The seven checks: `po_lookup`, `vendor_approved`, `po_status`, `total_tolerance`,
`line_reconciliation`, `tax_present`, `duplicate`. A failure maps to a severity
(`REJECT`/`FLAG`) via `policy_config`, so the same evidence can yield a different verdict
by editing data — no redeploy.

## Quickstart (local)

Use the project venv (`.venv/bin/python`); the base interpreter lacks deps.

```bash
docker compose up -d db                                  # Postgres on :5432
.venv/bin/python -m app.db.seed                          # schema + vendors/POs/policy
.venv/bin/python -m app.users.seed                       # 4 demo users
.venv/bin/python -m uvicorn app.main:app --reload        # API on :8000
API_BASE_URL=http://localhost:8000 .venv/bin/streamlit run ui/app.py   # UI on :8501
.venv/bin/python scripts/seed_demo_history.py            # seed the demo's starting state
```

The API self-applies the schema and seeds reference data + users on startup, so it also
works against an empty database with no extra steps. Set `ANTHROPIC_API_KEY` (env or
`.env`) for live extraction; without it, extraction falls back to the answer key for the
test fixtures.

## Project layout

```
app/
  extract/      PDF → JSON via Claude (text vs vision auto-detected)
  validate/     matcher (fuzzy PO/vendor) + the 7 checks → evidence report
  decide/       pure resolver (evidence + policy → verdict) + race-safe PO draw-down
  governance/   append-only trail (runs, events, reports, verdicts) + actor identity
  pipeline/     process_invoice — the single entry the API, UI, and worker all call
  ingest/       landing → process → archive/<YYYYMMDD> worker
  {auth,invoices,review,dashboard,policy,audit,admin}/  per-domain routers
ui/             Streamlit thin client over the API (no business logic)
scripts/        demo seeding + demo-invoice generation
tests/          pytest suite (skips DB/model tests cleanly when infra is absent)
```

## Docs

- **[docs/USAGE.md](docs/USAGE.md)** — using the app (roles, pages) + the demo walkthrough.
- **[docs/API.md](docs/API.md)** — endpoint reference and response schemas.
- **[docs/OPERATIONS.md](docs/OPERATIONS.md)** — deploy, CI/CD, monitoring, runbooks.
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — how it's built and deployed (Render).

## Tests

```bash
.venv/bin/python -m pytest -q              # full suite
.venv/bin/python validate_all.py --dry-run # 11-fixture matrix → 6 APPROVE / 3 FLAG / 2 REJECT
```

DB-backed tests skip when Postgres is down and live-model tests skip when
`ANTHROPIC_API_KEY` is unset, so the suite is always runnable.

## Stack

FastAPI (sync handlers) · PostgreSQL (psycopg3, raw SQL — no ORM) · Streamlit UI ·
Claude for extraction · Docker · Render (staging + production) with GitHub Actions CI/CD.

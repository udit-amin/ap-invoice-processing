# AP Invoice Processing

An auditable accounts-payable pipeline: a supplier invoice PDF goes in, and out
comes structured extraction data **plus** per-check validation evidence, with an
append-only governance trail recording every step. It handles machine-readable
and image-only (scanned) PDFs, itemised and bundled lines, separated and
embedded tax, fuzzy vendor/line matching, and race-proof duplicate detection.

The pipeline has three stages:

```
                 ┌─ extract ──┐   ┌─ match ──┐   ┌─ validate ─┐
   PDF ─ ingest ─┤  Claude →  │ → │ PO +     │ → │ 6 checks → │ → evidence report
                 │  JSON      │   │ vendor   │   │ pass/fail/ │    (no verdict)
                 └────────────┘   └──────────┘   │   skip     │
                                                  └────────────┘
        every stage emits an append-only governance event (Postgres)
```

**Separation of concerns:** this system *gathers evidence*; it does not decide
approve/reject. Each validation check answers one factual question. A later
decision engine reads the evidence plus governance thresholds and renders the
verdict — that is the only place a verdict is ever written.

## What's here

```
src/
  config.py               Model string, thresholds, DB DSN, paths
  pipeline.py             ingest → extract → match → validate orchestrator
  db/
    schema.sql            Postgres table definitions
    connection.py         psycopg connection pool + schema apply
    seed.py               Seed vendors, POs + line items, policy_config
  extract/
    api.py                FastAPI service (/extract, /process, /audit)
    ingest.py             Detect text vs image-only PDF
    text_extract.py       pdfplumber path
    vision_extract.py     PyMuPDF → base64 path
    prompts.py            Cached system instructions for Claude
    extractor.py          Extraction orchestrator — PDF in, JSON out
  validate/
    loader.py             Load POs + vendors from Postgres
    matcher.py            Fuzzy vendor / line-item matching (rapidfuzz)
    checks.py             The six validation checks
    validator.py          Runs checks → evidence report (no verdict)
  governance/
    recorder.py           Append-only audit trail (runs, events, reports)
  generate/
    invoice_generator.py     v0 synthetic PDFs (extraction tests)
    invoice_generator_v1.py  v1 synthetic PDFs (validation tests)
validate_all.py           CLI harness — runs all 9 invoices, prints the matrix
docker-compose.yml        Local Postgres
docs/
  MANUAL_TESTING.md       Step-by-step manual test guide
  QUERYING_THE_DB.md      How to inspect the database
tests/
  test_extraction.py      Extraction schema + behaviour (live, needs API key)
  test_validation.py      Pure validation logic (no infra)
  test_governance.py      Postgres + governance integration (skip-if-down)
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
python -m src.db.seed                        # schema + vendors, POs, policy
python -m src.generate.invoice_generator     # v0 PDFs (extraction tests)
python -m src.generate.invoice_generator_v1  # v1 PDFs (validation tests)
```

Override the database with `DATABASE_URL` (env or `.env`); it defaults to
`postgresql://ap:ap@localhost:5432/ap_invoices`.

> New here? See **[docs/MANUAL_TESTING.md](docs/MANUAL_TESTING.md)** for a
> guided walkthrough and **[docs/QUERYING_THE_DB.md](docs/QUERYING_THE_DB.md)**
> to inspect what the pipeline writes.

## Running the API

```bash
uvicorn src.extract.api:app --reload
```

| Endpoint | Purpose |
|----------|---------|
| `GET  /health` | Liveness + whether the API key is set |
| `POST /extract` | PDF → structured extraction JSON only |
| `POST /process` | PDF → full pipeline: extraction + validation evidence |
| `GET  /audit/{invoice_number}` | Ordered governance trail for an invoice |

```bash
curl -s -X POST http://localhost:8000/process \
  -F "file=@data/inputs/normal_1_dell.pdf" | python3 -m json.tool

curl -s http://localhost:8000/audit/DEL%2F2026%2F0412 | python3 -m json.tool
```

Every run is recorded to the append-only governance trail in Postgres
(`pipeline_runs`, `governance_events`, `validation_reports`). Audit writes are
best-effort — a logging failure never breaks the response.

## The validation checks

The validator runs six checks; each returns `pass | fail | skip` with a
human-readable reason. `skip` is a first-class outcome (the check could not run
meaningfully by design) — it is never collapsed into pass or fail.

| Check | Question it answers |
|-------|---------------------|
| `po_lookup` | Does the cited PO exist? (miss → short-circuits checks 3–5 to skip) |
| `vendor_approved` | Does the vendor fuzzy-match an **approved** registry entry? |
| `po_status` | Is the matched PO `open`? |
| `total_tolerance` | Is the invoice total within the PO's own `tolerance_pct`? |
| `line_reconciliation` | Do line qty/price reconcile? (bundled / embedded-tax fallbacks) |
| `duplicate` | First time we've seen this invoice? (race-proof via a DB unique constraint) |

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
  vendor, 0.80 line (`src/validate/matcher.py`).
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

## The validation matrix

Run the whole v1 invoice set through the pipeline and print the check matrix:

```bash
python validate_all.py --dry-run     # answer-key extraction, no LLM call
python validate_all.py               # live extraction (needs ANTHROPIC_API_KEY)
```

| Invoice | po_lookup | vendor | status | total_tol | line_recon | duplicate |
|---------|-----------|--------|--------|-----------|------------|-----------|
| normal_1…5 | pass | pass | pass | pass | pass | pass |
| edge_1 (scanned) | pass | pass | pass | pass | pass | pass |
| edge_2 (bundled) | pass | pass | pass | pass | **skip** | pass |
| edge_3 (embedded tax) | pass | pass | pass | pass | pass | pass |
| edge_4 (line mismatch) | pass | pass | pass | **pass** | **fail** | pass |

By default the harness resets operational state so the matrix is reproducible;
pass `--keep` to accumulate across runs (and watch `duplicate` flip to fail on a
second sighting).

## Tests

```bash
pytest tests/test_validation.py -v   # pure logic, no infra
pytest tests/test_governance.py -v   # Postgres integration (skips if DB down)
pytest tests/test_extraction.py -v   # extraction (live-model tests skip w/o key)
```

`test_validation.py` always runs (it injects in-memory reference data).
`test_governance.py` skips cleanly when Postgres is unreachable. Live-model
extraction tests skip cleanly when `ANTHROPIC_API_KEY` is not set.

## Stack

Python 3.11+ · FastAPI + uvicorn · anthropic SDK (Sonnet, prompt caching) ·
**Postgres + psycopg3** · rapidfuzz · pdfplumber · PyMuPDF + Pillow · reportlab ·
pytest + httpx

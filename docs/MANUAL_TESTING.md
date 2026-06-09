# Manual Testing Guide

A step-by-step walkthrough to exercise the pipeline by hand and confirm it
behaves as designed. Pairs with **[QUERYING_THE_DB.md](QUERYING_THE_DB.md)** for
inspecting what each step writes to Postgres.

Everything below assumes you are in the repo root with the virtualenv active:

```bash
source .venv/bin/activate
```

---

## 1. One-time setup

```bash
pip install -r requirements.txt

# Local Postgres (DSN: postgresql://ap:ap@localhost:5432/ap_invoices)
docker compose up -d

# Apply schema + seed reference data (vendors, POs + line items, policy)
python -m src.db.seed

# Generate the synthetic test invoices
python -m src.generate.invoice_generator       # v0 set (extraction tests)
python -m src.generate.invoice_generator_v1    # v1 set (validation tests)
```

`python -m src.db.seed` prints a summary; expect **10 vendors (9 approved), 10
purchase orders (1 closed), 16 line items, 1 policy row**.

An Anthropic API key is optional. Without it, use `--dry-run` and the
answer-key extraction; the LLM-backed extraction tests skip cleanly.

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # optional
```

---

## 2. Run the automated suites

```bash
# Pure validation logic — no database, no API key required
pytest tests/test_validation.py -v        # expect: all pass

# Postgres + governance integration — needs the DB up
pytest tests/test_governance.py -v         # expect: all pass

# Extraction — live-model tests skip without an API key
pytest tests/test_extraction.py -v
```

**Prove the skip-if-down safety net.** Stop Postgres and rerun the first two:

```bash
docker compose stop
pytest tests/test_validation.py tests/test_governance.py
#   → test_validation.py all pass, test_governance.py all SKIPPED
docker compose start            # bring it back up before continuing
```

---

## 3. Run the end-to-end harness

This runs all nine v1 invoices through the full pipeline and prints the check
matrix. `--dry-run` uses the answer key instead of calling the model.

```bash
python validate_all.py --dry-run
```

What to verify in the output:

- Every `normal_*` and `edge_1`/`edge_3` row is all `✓`.
- `edge_2_techgear_bundled` → `line_reconciliation` is `–` (**skip**, bundled).
- `edge_4_dell_line_mismatch` → `total_tolerance` is `✓` but
  `line_reconciliation` is `✗` (**fail**), with `qty_and_price_variance` detail
  printed underneath. This is the case that proves line reconciliation catches
  what total matching misses.

By default the harness resets operational tables first so the matrix is
reproducible. Use `--keep` to accumulate across runs (see next step).

---

## 4. Prove race-proof duplicate detection

Run the harness twice **keeping** state between runs:

```bash
python validate_all.py --dry-run            # clean run: all duplicate = ✓
python validate_all.py --dry-run --keep     # second run: duplicate flips to ✗
```

On the second run every invoice's `duplicate` column flips to `✗`, each citing
the original run UUID and date (e.g. *"Duplicate of run ac9410ab-… on
2026-06-09"*). The flip is enforced by a `UNIQUE(invoice_number, vendor_name)`
constraint in the database, so two concurrent runs of the same invoice cannot
both pass.

To start clean again, drop `--keep` (the next plain run truncates operational
state), or reset manually — see [QUERYING_THE_DB.md](QUERYING_THE_DB.md#resetting-operational-state).

---

## 5. Exercise the API

Start the server in one terminal:

```bash
uvicorn src.extract.api:app --reload
```

Interactive docs are at <http://localhost:8000/docs>. In another terminal:

```bash
# Liveness
curl -s http://localhost:8000/health | python3 -m json.tool

# Extract only (structured JSON; no validation)
curl -s -X POST http://localhost:8000/extract \
  -F "file=@data/inputs/normal_1_dell.pdf" | python3 -m json.tool

# Full pipeline: extraction + validation evidence + governance run
curl -s -X POST http://localhost:8000/process \
  -F "file=@data/inputs/edge_4_dell_line_mismatch.pdf" | python3 -m json.tool

# Audit trail for an invoice (note: '/' is URL-encoded as %2F)
curl -s http://localhost:8000/audit/DEL%2F2026%2F0419 | python3 -m json.tool
```

> `/extract` and `/process` call the live model, so they need
> `ANTHROPIC_API_KEY`. Without a key they return a structured `error` payload
> (HTTP 200) rather than crashing.

What to verify:

- `/process` returns `{run_id, extraction, validation}`. For
  `edge_4_dell_line_mismatch`, the `validation` block shows
  `total_tolerance: pass` and `line_reconciliation: fail`.
- `/audit/{invoice_number}` returns the ordered event trail
  (`ingest → extract → match → match → validate × 4`) plus the latest
  validation report. A never-seen invoice number returns **404**.

---

## 6. Expected outcomes at a glance

| What you did | Expected result |
|--------------|-----------------|
| `python -m src.db.seed` | 10 vendors / 10 POs / 16 lines / 1 policy |
| `pytest tests/test_validation.py` | all pass, no infra needed |
| `pytest tests/test_governance.py` (DB up) | all pass |
| `pytest tests/test_governance.py` (DB down) | all skipped |
| `validate_all.py --dry-run` | matrix with edge_2 skip, edge_4 line-recon fail |
| `validate_all.py --dry-run --keep` (2nd run) | duplicate flips to fail |
| `GET /audit/{unknown}` | HTTP 404 |

---

## 7. Teardown

```bash
docker compose down          # stop Postgres, keep the data volume
docker compose down -v       # stop and DELETE all data (full reset)
```

After `down -v`, re-run the setup in §1 to recreate and reseed.

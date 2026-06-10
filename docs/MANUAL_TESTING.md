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

`python -m src.db.seed` prints a summary; expect **11 vendors (10 approved), 10
purchase orders (1 closed), 16 line items, 1 policy row**. (The `edge_5`/`edge_6`
PDFs ship in `data/inputs/`; they aren't produced by the generators.)

An Anthropic API key is optional. Without it, use `--dry-run` and the
answer-key extraction; the LLM-backed extraction tests skip cleanly.

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # optional
```

---

## 2. Run the automated suites

```bash
# Pure logic — no database, no API key required
pytest tests/test_validation.py -v         # expect: all pass
pytest tests/test_decision.py -v           # pure matrix passes; DB tests skip if down

# Postgres integration — needs the DB up
pytest tests/test_governance.py -v         # expect: all pass

# Extraction — live-model tests skip without an API key
pytest tests/test_extraction.py -v
```

**Prove the skip-if-down safety net.** Stop Postgres and rerun:

```bash
docker compose stop
pytest tests/test_validation.py tests/test_decision.py tests/test_governance.py
#   → pure tests pass; the DB-backed tests SKIP cleanly
docker compose start            # bring it back up before continuing
```

---

## 3. Run the end-to-end harness

This runs all eleven invoices through the full pipeline (ingest → extract →
match → validate → **decide**) and prints the check matrix plus a **Verdict**
column. `--dry-run` uses the answer key instead of calling the model.

```bash
python validate_all.py --dry-run
```

What to verify in the verdict column:

- `normal_1…5` and `edge_3` → **APPROVE**.
- `edge_1` (scanned) → **FLAG** — confidence below the 0.75 gate.
- `edge_2` (bundled, ₹8.02L) → **FLAG** — over the ₹7.5L authority ceiling;
  `line_reconciliation` is `–` (skip), which alone does *not* flag.
- `edge_4` → **FLAG** — `total_tolerance` is `✓` but `line_reconciliation` is
  `✗`; line reconciliation catches what total matching misses.
- `edge_5` (Globex → PO-9999) → **REJECT** — `po_lookup` ✗ + `vendor_approved` ✗.
- `edge_6` (closed PO-5003) → **REJECT** — `po_status` ✗ (clean single driver).

The per-invoice reason lines below the matrix should show three distinct FLAG
reasons (confidence, authority, line variance) and two distinct REJECT reasons.

The matrix is per-invoice in isolation: the harness reseeds PO balances before
each invoice so one approval's decrement doesn't change another's verdict. By
default it also resets operational tables for reproducibility; `--keep`
accumulates across runs (next step).

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

## 5. Prove the decision-engine guarantees

**Policy is data, not code (AC#5).** Lower the auto-approve ceiling and the
normals flip to FLAG with no code change:

```bash
docker exec ap_invoices_db psql -U ap -d ap_invoices \
  -c "UPDATE policy_config SET auto_approve_ceiling=200000 WHERE id=1;"
python validate_all.py --dry-run     # normal_1 (₹5.66L) now FLAG (over_authority)
docker exec ap_invoices_db psql -U ap -d ap_invoices \
  -c "UPDATE policy_config SET auto_approve_ceiling=750000 WHERE id=1;"   # restore
```

**APPROVE decrements the PO; a stale approve downgrades (AC#4).** Process a clean
Dell invoice live — it APPROVEs and draws PO-5001 to zero, closing it:

```bash
python -m src.db.seed     # restore PO-5001 balance to 566400/open
curl -s -X POST localhost:8000/process -F file=@data/inputs/normal_1_dell.pdf \
  | python3 -c "import sys,json; d=json.load(sys.stdin)['decision']; print(d['verdict'], d['po_balance_after'])"
# → APPROVE 0.0
docker exec ap_invoices_db psql -U ap -d ap_invoices \
  -c "SELECT remaining_balance, status FROM purchase_orders WHERE po_id='PO-5001';"
# → 0.00 | closed
```

The race-safe downgrade (a second invoice that would over-commit the PO becomes
FLAG inside the commit lock, never over-drawing) is covered deterministically by
`pytest tests/test_decision.py -k downgrade`.

---

## 6. Exercise the API

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

- `/process` returns `{run_id, extraction, validation, decision}`. For
  `edge_4_dell_line_mismatch`, `validation` shows `total_tolerance: pass` and
  `line_reconciliation: fail`, and `decision.verdict` is **FLAG** with
  `review_payload.queue == "line_variance"`.
- `/audit/{invoice_number}` returns the ordered event trail
  (`ingest → extract → match → match → validate ×4 → decision`) plus the latest
  validation report and `latest_verdict`. A never-seen invoice → **404**.

---

## 7. Expected outcomes at a glance

| What you did | Expected result |
|--------------|-----------------|
| `python -m src.db.seed` | 11 vendors / 10 POs / 16 lines / 1 policy |
| `pytest tests/test_validation.py` | all pass, no infra needed |
| `pytest tests/test_decision.py` (DB up) | all pass (pure + commit) |
| `pytest tests/test_decision.py` (DB down) | pure pass, commit tests skipped |
| `pytest tests/test_governance.py` (DB up / down) | all pass / all skipped |
| `validate_all.py --dry-run` | verdicts: 6 APPROVE, 3 FLAG, 2 REJECT |
| `validate_all.py --dry-run --keep` (2nd run) | every verdict → REJECT (duplicate) |
| lower `auto_approve_ceiling` to 200000 | normals flip APPROVE → FLAG |
| live `/process` of normal_1 | APPROVE, PO-5001 balance → 0 / closed |
| `GET /audit/{unknown}` | HTTP 404 |

---

## 8. Teardown

```bash
docker compose down          # stop Postgres, keep the data volume
docker compose down -v       # stop and DELETE all data (full reset)
```

After `down -v`, re-run the setup in §1 to recreate and reseed.

# Querying the Database

How to connect to Postgres and inspect what the pipeline records — reference
data, processed runs, validation reports, and the governance audit trail.

## Connecting

The database runs in the `ap_invoices_db` container from `docker-compose.yml`.
Any of these work:

```bash
# Via the container (no local psql needed)
docker exec -it ap_invoices_db psql -U ap -d ap_invoices

# Via a local psql client against the published port
psql postgresql://ap:ap@localhost:5432/ap_invoices

# One-off query without an interactive shell
docker exec ap_invoices_db psql -U ap -d ap_invoices -c "SELECT count(*) FROM governance_events;"
```

A GUI client (DBeaver, TablePlus, pgAdmin) works too — host `localhost`, port
`5432`, database `ap_invoices`, user/password `ap` / `ap`.

Handy psql meta-commands: `\dt` (list tables), `\d governance_events` (describe
a table), `\x` (toggle expanded row output — useful for JSONB), `\q` (quit).

## Tables at a glance

| Table | What it holds |
|-------|---------------|
| `vendors` | Vendor registry (V-001…V-010); `approved` flags |
| `purchase_orders` | POs (PO-5001…PO-5010); balance, status, per-PO `tolerance_pct` |
| `po_line_items` | Expected line items per PO (for line reconciliation) |
| `policy_config` | Single-row governance (auto-approve ceiling, default tolerance, confidence threshold) |
| `pipeline_runs` | One row per end-to-end execution (`run_id` UUID) |
| `invoices` | Dedup ledger — `UNIQUE(invoice_number, vendor_name)` |
| `validation_reports` | Full evidence report per run, as JSONB |
| `governance_events` | Append-only audit trail: one+ row per stage |
| `verdicts` | The decision per run: verdict, reason, drivers, `po_balance_after` |

`vendors` / `purchase_orders` / `po_line_items` / `policy_config` are seeded
reference data. The rest are written at runtime by the pipeline.

---

## Reference data

```sql
-- Vendors and approval status
SELECT vendor_id, vendor_name, approved, category FROM vendors ORDER BY vendor_id;

-- POs with their per-PO tolerance and balance
SELECT po_id, vendor_name, status, remaining_balance, tolerance_pct
FROM purchase_orders ORDER BY po_id;

-- Expected line items for one PO
SELECT line_no, description, quantity, unit_price
FROM po_line_items WHERE po_id = 'PO-5001' ORDER BY line_no;

-- Governance policy (single row): ceiling, confidence gate, version, overrides
SELECT auto_approve_ceiling, min_confidence, policy_version, severity_overrides
FROM policy_config;
```

Policy is read fresh on every decision, so changing it here flips verdicts with
no code change:

```sql
-- Lower the auto-approve ceiling → invoices over it FLAG (over_authority)
UPDATE policy_config SET auto_approve_ceiling = 200000 WHERE id = 1;
-- Make a tolerance failure a hard REJECT instead of a FLAG (data-driven map)
UPDATE policy_config SET severity_overrides = '{"total_tolerance":"REJECT"}' WHERE id = 1;
```

---

## Pipeline runs

```sql
-- Most recent runs
SELECT run_id, invoice_number, vendor_name, source_type, overall_conf,
       started_at, finished_at
FROM pipeline_runs
ORDER BY started_at DESC
LIMIT 20;

-- How many runs per invoice (reprocessing shows >1)
SELECT invoice_number, count(*) AS runs
FROM pipeline_runs
GROUP BY invoice_number
ORDER BY runs DESC;
```

---

## Governance audit trail

```sql
-- Event volume by stage and status (sanity check after a harness run)
SELECT stage, status, count(*)
FROM governance_events
GROUP BY stage, status
ORDER BY stage, status;

-- The full ordered trail for one run
SELECT stage, status, detail, created_at
FROM governance_events
WHERE run_id = '<paste-a-run_id-here>'
ORDER BY event_id;
```

**Reconstruct everything for one invoice** (all its runs and their events, in
order) — this is what `GET /audit/{invoice_number}` returns:

```sql
SELECT r.run_id, e.stage, e.status, e.detail, e.created_at
FROM pipeline_runs r
JOIN governance_events e ON e.run_id = r.run_id
WHERE r.invoice_number = 'DEL/2026/0419'
ORDER BY r.started_at, e.event_id;
```

Reading JSONB detail fields directly:

```sql
-- Extraction confidence captured at the extract stage
SELECT run_id, detail->>'source_type' AS source, detail->>'overall_conf' AS conf
FROM governance_events
WHERE stage = 'extract';

-- Every check that failed, with its reason (use \x for readable output)
SELECT detail->>'check' AS check, detail->>'reason' AS reason, run_id
FROM governance_events
WHERE stage IN ('match', 'validate') AND status = 'fail'
ORDER BY run_id;
```

---

## Validation reports

The whole evidence report is stored as JSONB, with the pass/fail/skip counts
broken out into columns.

```sql
-- Latest report per invoice with its summary counts
SELECT invoice_number, passed, failed, skipped, created_at
FROM validation_reports
ORDER BY created_at DESC;

-- Pull a full report (expanded output recommended: \x on)
SELECT report
FROM validation_reports
WHERE invoice_number = 'DEL/2026/0419'
ORDER BY created_at DESC
LIMIT 1;

-- Invoices that had any line-reconciliation failure, drilling into the JSONB
SELECT vr.invoice_number, c->>'reason' AS reason
FROM validation_reports vr,
     jsonb_array_elements(vr.report->'checks') AS c
WHERE c->>'check' = 'line_reconciliation'
  AND c->>'status' = 'fail';

-- Per-line mismatch detail for a failing reconciliation
SELECT d->>'invoice_line'   AS invoice_line,
       d->>'classification' AS classification,
       d->'invoice'         AS invoice_side,
       d->'po'              AS po_side
FROM validation_reports vr,
     jsonb_array_elements(vr.report->'checks') AS c,
     jsonb_array_elements(c->'detail')         AS d
WHERE vr.invoice_number = 'DEL/2026/0419'
  AND c->>'check' = 'line_reconciliation';
```

---

## Duplicate-detection ledger

```sql
-- What the dedup constraint is keyed on; first_run_id points at the original run
SELECT invoice_number, vendor_name, first_run_id, first_seen_at
FROM invoices
ORDER BY first_seen_at;
```

A second pipeline run of an invoice already in this table fails the `duplicate`
check rather than inserting a new row.

---

## Verdicts (decision engine)

```sql
-- Latest verdict per invoice with the reason and (on APPROVE) the new PO balance
SELECT invoice_number, verdict, po_balance_after, policy_version, reason
FROM verdicts
ORDER BY decided_at DESC;

-- Verdict distribution
SELECT verdict, count(*) FROM verdicts GROUP BY verdict ORDER BY verdict;

-- Why was this flagged/rejected? Each driver's contribution (\x recommended)
SELECT d->>'signal'   AS signal,
       d->>'outcome'  AS outcome,
       d->>'severity' AS severity,
       d->>'detail'   AS detail
FROM verdicts v, jsonb_array_elements(v.drivers) AS d
WHERE v.invoice_number = 'DEL/2026/0419'
ORDER BY severity DESC;

-- The human-review queue: everything that needs a person, with what to check
SELECT invoice_number,
       review_payload->>'queue'         AS queue,
       review_payload->>'what_to_check' AS what_to_check
FROM verdicts
WHERE requires_human_review
ORDER BY decided_at DESC;
```

The verdict and its PO balance decrement are written in one transaction, so a
`verdicts` row with a non-null `po_balance_after` always matches the PO's
`remaining_balance` change.

---

## Resetting operational state

Wipe runtime data but keep the seeded reference data — handy between manual test
runs. (Reseed afterwards if you want APPROVE-decremented PO balances restored:
`python -m src.db.seed`.)

```sql
TRUNCATE governance_events, validation_reports, verdicts, invoices, pipeline_runs
  RESTART IDENTITY CASCADE;
```

Or from the shell:

```bash
docker exec ap_invoices_db psql -U ap -d ap_invoices \
  -c "TRUNCATE governance_events, validation_reports, verdicts, invoices, pipeline_runs RESTART IDENTITY CASCADE;"
```

To reset *everything* including reference data, recreate the container and
reseed:

```bash
docker compose down -v && docker compose up -d && python -m src.db.seed
```

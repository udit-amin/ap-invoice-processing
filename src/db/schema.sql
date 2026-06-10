-- Postgres schema for the AP invoice processor (v2).
--
-- Single source of truth for reference data, processed invoices, validation
-- reports, and the append-only governance audit trail.  Governance lives in
-- data (policy_config / per-PO tolerance), never in code.
--
-- Idempotent: safe to run repeatedly (CREATE TABLE IF NOT EXISTS).  Use
-- src/db/seed.py to (re)load reference data.

-- --------------------------------------------------------------------------
-- Reference data (seeded)
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vendors (
    vendor_id    TEXT PRIMARY KEY,            -- e.g. V-001
    vendor_name  TEXT NOT NULL UNIQUE,
    approved     BOOLEAN NOT NULL,            -- V-010 is intentionally unapproved
    category     TEXT NOT NULL,
    tax_id       TEXT,                        -- GSTIN-style
    terms        TEXT                         -- e.g. Net-30
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    po_id              TEXT PRIMARY KEY,       -- e.g. PO-5001
    vendor_id          TEXT REFERENCES vendors (vendor_id),
    vendor_name        TEXT NOT NULL,
    description        TEXT,
    approved_amount    NUMERIC(14, 2) NOT NULL,
    remaining_balance  NUMERIC(14, 2) NOT NULL,
    status             TEXT NOT NULL,          -- open | closed | cancelled
    tolerance_pct      NUMERIC(5, 2) NOT NULL  -- per-PO; governance in data
);

-- Expected line items, normalised out of the old expected_line_items array.
CREATE TABLE IF NOT EXISTS po_line_items (
    id           BIGSERIAL PRIMARY KEY,
    po_id        TEXT NOT NULL REFERENCES purchase_orders (po_id) ON DELETE CASCADE,
    line_no      INTEGER NOT NULL,
    description  TEXT NOT NULL,
    quantity     NUMERIC(14, 3),
    unit_price   NUMERIC(14, 2)
);
CREATE INDEX IF NOT EXISTS idx_po_line_items_po ON po_line_items (po_id);

-- Single-row governance policy.
CREATE TABLE IF NOT EXISTS policy_config (
    id                    INTEGER PRIMARY KEY CHECK (id = 1),
    auto_approve_ceiling  NUMERIC(14, 2) NOT NULL,   -- INR, pre-tax
    default_tolerance_pct NUMERIC(5, 2)  NOT NULL,
    confidence_threshold  NUMERIC(4, 3)  NOT NULL
);

-- --------------------------------------------------------------------------
-- Operational data
-- --------------------------------------------------------------------------

-- One row per end-to-end pipeline execution. Reprocessing creates a new row.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id          UUID PRIMARY KEY,
    invoice_number  TEXT,
    vendor_name     TEXT,
    po_reference    TEXT,
    source_type     TEXT,                       -- text | scanned
    invoice_path    TEXT,
    overall_conf    NUMERIC(4, 3),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ
);

-- Duplicate-detection ledger. The UNIQUE constraint makes dedup race-proof:
-- the second concurrent insert of the same invoice loses the conflict.
CREATE TABLE IF NOT EXISTS invoices (
    invoice_id      BIGSERIAL PRIMARY KEY,
    invoice_number  TEXT NOT NULL,
    vendor_name     TEXT NOT NULL DEFAULT '',
    first_run_id    UUID REFERENCES pipeline_runs (run_id),
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (invoice_number, vendor_name)
);

-- Full validation report (evidence only — never a verdict) as JSONB.
CREATE TABLE IF NOT EXISTS validation_reports (
    id              BIGSERIAL PRIMARY KEY,
    run_id          UUID REFERENCES pipeline_runs (run_id),
    invoice_number  TEXT,
    report          JSONB NOT NULL,
    passed          INTEGER,
    failed          INTEGER,
    skipped         INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_validation_reports_run ON validation_reports (run_id);
CREATE INDEX IF NOT EXISTS idx_validation_reports_invoice ON validation_reports (invoice_number);

-- --------------------------------------------------------------------------
-- Governance audit trail (append-only)
-- --------------------------------------------------------------------------
-- One or more events per pipeline stage: ingest -> extract -> match ->
-- validate -> (decision). Never updated or deleted.
CREATE TABLE IF NOT EXISTS governance_events (
    event_id    BIGSERIAL PRIMARY KEY,
    run_id      UUID REFERENCES pipeline_runs (run_id),
    stage       TEXT NOT NULL,                  -- ingest|extract|match|validate|decision
    status      TEXT NOT NULL,                  -- ok|fail|skip|warn|error
    detail      JSONB,
    actor       TEXT NOT NULL DEFAULT 'system',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_governance_events_run ON governance_events (run_id);
CREATE INDEX IF NOT EXISTS idx_governance_events_stage ON governance_events (stage);

-- --------------------------------------------------------------------------
-- Decision engine (v3)
-- --------------------------------------------------------------------------
-- Governance policy columns added for the decision engine. The existing
-- single policy row is backfilled by the seed upsert.
ALTER TABLE policy_config ADD COLUMN IF NOT EXISTS min_confidence     NUMERIC(4, 3);
ALTER TABLE policy_config ADD COLUMN IF NOT EXISTS policy_version     TEXT;
ALTER TABLE policy_config ADD COLUMN IF NOT EXISTS severity_overrides JSONB;

-- The verdict — the one place a verdict is written. Evidence (validation_reports)
-- stays separate from the verdict (this table); the two are joined by run_id.
CREATE TABLE IF NOT EXISTS verdicts (
    verdict_id                   BIGSERIAL PRIMARY KEY,
    run_id                       UUID REFERENCES pipeline_runs (run_id),
    invoice_number               TEXT,
    po_reference                 TEXT,
    verdict                      TEXT NOT NULL,        -- APPROVE | FLAG | REJECT
    reason                       TEXT NOT NULL,
    drivers                      JSONB NOT NULL,
    requires_human_review        BOOLEAN NOT NULL,
    review_payload               JSONB,
    confidence_overall           NUMERIC(4, 3),
    policy_version               TEXT,
    auto_approve_ceiling_applied NUMERIC(14, 2),
    po_balance_after             NUMERIC(14, 2),       -- non-null only on APPROVE
    decided_at                   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_verdicts_invoice ON verdicts (invoice_number);
CREATE INDEX IF NOT EXISTS idx_verdicts_run ON verdicts (run_id);

-- v0 schema for the AP invoice processor.
-- Only the tables the extraction module needs to be exercised and to leave
-- clean seams for later versions (matching, validation, decisioning, audit).
-- Governance lives in data (policy_config / per-PO tolerance), never in code.

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS run_log;
DROP TABLE IF EXISTS purchase_orders;
DROP TABLE IF EXISTS vendors;
DROP TABLE IF EXISTS policy_config;

-- Vendor registry (handover §6.1).
CREATE TABLE vendors (
    vendor_id    TEXT PRIMARY KEY,         -- e.g. V-01
    vendor_name  TEXT NOT NULL UNIQUE,
    approved     INTEGER NOT NULL,         -- 0/1; V-10 is intentionally unapproved
    category     TEXT NOT NULL,
    tax_id       TEXT NOT NULL,            -- GSTIN-style
    terms        TEXT NOT NULL             -- e.g. Net-30
);

-- Purchase orders (handover §6.2). Amounts are INR, pre-tax.
-- tolerance_pct is per-PO so governance lives in data, not code.
CREATE TABLE purchase_orders (
    po_id              TEXT PRIMARY KEY,    -- e.g. PO-4421
    vendor_name        TEXT NOT NULL,
    description        TEXT NOT NULL,
    approved_amount    REAL NOT NULL,
    remaining_balance  REAL NOT NULL,
    status             TEXT NOT NULL,       -- open | closed
    tolerance_pct      REAL NOT NULL,
    FOREIGN KEY (vendor_name) REFERENCES vendors (vendor_name)
);

-- Single-row policy. Tolerance default + auto-approve ceiling + confidence
-- threshold are stored here so later versions read them, never hardcode.
CREATE TABLE policy_config (
    id                    INTEGER PRIMARY KEY CHECK (id = 1),
    auto_approve_ceiling  REAL NOT NULL,    -- INR, pre-tax
    default_tolerance_pct REAL NOT NULL,
    confidence_threshold  REAL NOT NULL
);

-- Append-only run log. Created empty in v0; the extractor leaves the seam but
-- does not yet write here (matching/decisioning fill the later columns).
CREATE TABLE run_log (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    invoice_path    TEXT,
    source_type     TEXT,                   -- text | scanned
    extracted_json  TEXT,                   -- full §5.3 payload
    overall_conf    REAL,
    -- seams for later versions (left null/unused in v0):
    matched_po      TEXT,
    decision        TEXT,                   -- approve | review | reject
    notes           TEXT
);

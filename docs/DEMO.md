# Demo guide — operational flow, 5-minute video script, live runbook

Everything needed to record the case-study video and run the process live in the
interview. For *how it's built* see [ARCHITECTURE.md](ARCHITECTURE.md); for *how to
operate it* see [OPERATIONS.md](OPERATIONS.md); for endpoints, [API.md](API.md).

The submission is two things: **(1)** the live link (the deployed Streamlit URL) and
**(2)** a ≤5-minute demo video (happy path + ≥1 edge case, narrated).

---

## 1. Operational flow (the one-pager to narrate)

```
        ┌─────────── ingestion ───────────┐
 email → S3 landing  →  worker sweeps  →  ┐
 (or)  UI upload / Batch ingest  ─────────┤
                                          ▼
   extract ─→ match ─→ validate ─→ decide ─→ commit ─→ governance trail
   (Claude)  (PO +    (7 checks →  (pure,    (race-safe   (append-only,
              vendor)  evidence)    policy)   PO draw-down) who/what/when)
                                          │
                            APPROVE ──────┤────── FLAG / REJECT
                          (PO drawn down) │   (human review queue)
                                          ▼
                          archive → s3://…/YYYYMMDD/  (worker path)
```

1. **Ingestion.** Invoices arrive as PDFs. In production an email→S3 *landing* bucket
   (partitioned by date) drops files; the worker (`app/ingest/worker.py`) sweeps,
   processes each, and moves it to an *archive* bucket under the same `<YYYYMMDD>`. On
   the **Render demo** there's no S3, so a batch enters via the UI's **Batch ingest**
   (multi-file upload); the **Run view** (single) and the worker all call the *same*
   `process_invoice`, so every path produces an identical trail.
2. **Extract** — Claude reads the PDF (text vs scanned auto-detected; scans take the
   vision path). **This is the only LLM step.**
3. **Match** — fuzzy-match to the PO and the approved-vendor registry.
4. **Validate** — 7 checks (po_lookup, vendor_approved, po_status, total_tolerance,
   line_reconciliation, tax_present, duplicate) produce an **evidence report — never a
   verdict**.
5. **Decide** — a **pure, deterministic, policy-driven** resolver turns evidence +
   confidence + policy into `APPROVE | FLAG | REJECT`. Reproducible byte-for-byte; the
   reason text is generated here, not by the model.
6. **Commit + trail** — APPROVE draws the PO down via a race-safe `SELECT … FOR UPDATE`;
   everything is written to an append-only governance trail (who, what, when).
7. **Human-in-the-loop** — FLAG/REJECT land in the **Review queue** (three framings by
   flag type); any auto-decision can be overridden in **Processed**. Clerks see their own
   runs; managers see all and edit **Policy** (data, not code — changes the next verdict
   with no redeploy).

**Why it's safe to automate:** deterministic decisions + duplicate/over-ceiling/
unapproved safeguards + a complete audit trail mean automation handles the volume while
humans only touch the exceptions.

---

## 2. The 5-minute video script (beat sheet + narration)

Total 5:00. Record at the **deployed URL** (warm it first — see §4). Have the four
`data/demo_live/` PDFs on the desktop, ready to drag.

| Time | On screen | Say (paraphrase) |
|---|---|---|
| **0:00–0:25** | Login page → sign in as **Priya (clerk)** | "An AP team gets hundreds of vendor PDFs a month — someone opens each, finds the PO, checks the numbers, decides. I built a process that turns an invoice PDF into a *reasoned, auditable decision* — APPROVE, FLAG, or REJECT — every step visible. It's live; let me run it." |
| **0:25–1:50** | **Run view** → drag **`demo_approve_dell.pdf`** → Process → 7 stages animate → APPROVE card → expand **"What the system saw"** | "A Dell invoice. Watch the real stages — it's *read* by Claude, the **only** AI step; then we find the PO, check the vendor, validate amounts and line items, check tax, and decide. APPROVE. The reason and these seven checks come from a **deterministic** engine, not the model — so the verdict is reproducible and explainable, which is the whole point for finance. Here's the source PDF it read." |
| **1:50–2:55** | **Batch ingest** → drag **all four** `data/demo_live` PDFs → Process → results table | "But it's not one-at-a-time — this is the volume play. A batch in one go, same pipeline. Look at the table: an **approve**; a **reject** — unapproved vendor; one **flagged** over the approval ceiling; and one **flagged for no tax** — a rule I added, a registered vendor with no GST gets routed to a human, never auto-paid. In production these land in an **S3 bucket partitioned by date** and a worker sweeps them on a schedule; here I just upload the batch." |
| **2:55–3:55** | Log out → in as **Anjali (manager)** → **Dashboard** (STP, flags-by-reason, trend) → click a run → **audit trail**; then **Review queue** (open the scanned low-confidence + a line-variance item) | "The manager sees the whole operation: straight-through rate, what's getting flagged and why, the trend — and every run has a full **audit trail**: who decided what, when. The queue gives the reviewer the right view per problem: here a **scanned** invoice where confidence dropped so it routed to a human; here invoice **lines side-by-side** with the PO." |
| **3:55–4:30** | **Policy** → lower the auto-approve ceiling | "Policy is **data**, not code — I lower the ceiling here and the next invoice respects it, no redeploy. That's the judgment layer, tunable by the business." |
| **4:30–5:00** | Architecture diagram (ARCHITECTURE.md) | "In production the same pipeline sits behind that S3 landing→archive worker on AWS — the UI is one way in. And it's deployed for real: one container, staging and production on Render, CI/CD runs the test suite before every deploy. Invoice in, explainable decision out, humans only on the exceptions." |

**Cut order if over 5:00:** (1) the Policy beat (say it, don't click); (2) the scan in
the queue (keep the line-variance side-by-side). **Never cut:** the live happy-path run
and the batch (those are the explicit grading bar — happy path + edge cases).

---

## 3. Edge cases (what they demonstrate — for the Q&A)

These are deliberate, not trivial — each maps to a line in the problem statement:

| Edge case | Fixture / demo file | Verdict | What it proves |
|---|---|---|---|
| Unapproved vendor | `demo_reject_globex.pdf` / `edge_5_globex` | **REJECT** | Approved-vendor registry enforced |
| Over auto-approve authority | `demo_flag_techgear.pdf` / `edge_2_techgear` | **FLAG** | Spend authority gate (₹7.5L ceiling) |
| Scanned image (no text layer) | `edge_1_greenleaf_scanned` | **FLAG** (low-conf) | Vision path + confidence gate → human |
| Line items don't reconcile | `edge_4_dell_line_mismatch` | **FLAG** | Line-level matching, not just totals |
| Bundled line, no unit price | `edge_2_techgear_bundled` | **FLAG** | Tolerant of format variation |
| Tax embedded in line prices | `edge_3_blueprint_embedded_tax` | **APPROVE** | Handles embedded vs separated tax |
| **No tax on the invoice** | `demo_flag_notax.pdf` (FastFreight / PO-5011) | **FLAG** | `tax_present` — tax must be declared (else flag) |
| Closed PO | `edge_6_cloudhost_closed_po` | **REJECT** | PO status enforced |
| Duplicate re-send | any re-upload | **REJECT** | `UNIQUE(invoice_number, vendor_name)` safeguard |

The verified live-upload set (`data/demo_live/`, regenerated by
`scripts/make_live_demo_invoices.py`): **Dell → APPROVE**, **Globex → REJECT**,
**TechGear → FLAG** (over ceiling), **FastFreight no-tax → FLAG** (missing tax) — all
confirmed end-to-end through the real pipeline. The no-tax one flags *only* on
`tax_present` (queue `missing_tax`), since PO-5011 is stored ex-tax so every other
check passes.

---

## 4. Live runbook — before you hit record / join the call

1. **Warm the deployment.** Free Render services sleep after ~15 min idle (~30–60 s cold
   start). Open both URLs a minute before; confirm the UI loads and the dashboard has data.
2. **Reset to a clean state** (so the happy path is a clean APPROVE, not a duplicate).
   Easiest: log in as a manager → **Dashboard → ⚠️ Demo controls → Reset demo data**
   (truncates + reseeds 5 days of back-dated history; verdict mix returns to **6/3/2**).
   Equivalent CLI, from your laptop against the database's **External URL**:
   `DATABASE_URL='<external-url>?sslmode=require' .venv/bin/python scripts/seed_demo_history.py`.
3. **Have the live-upload PDFs ready** on your desktop: `data/demo_live/*.pdf`
   (run `scripts/make_live_demo_invoices.py` if they're not minted yet).
4. **Logins:** `priya@zamp.ai` / `demo-clerk-1` (clerk) · `anjali@zamp.ai` / `demo-mgr-1`
   (manager).
5. **Rehearse once, end to end**, against the live URL, in the exact order above.

**Gotchas to avoid on camera**
- Re-uploading a *seeded* fixture → REJECT (duplicate). Use the `demo_live/` files for the
  clean APPROVE, or show duplicate detection on purpose.
- A rehearsal APPROVE draws its PO down — reseed (step 2) before the real take so the live
  APPROVE doesn't downgrade.
- Cold start: if the first click spins, narrate over it ("free tier waking up") — or pay one
  month of the Starter tier for the grading week to keep both services always-on.

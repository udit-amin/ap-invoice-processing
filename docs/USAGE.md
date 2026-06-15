# Usage guide

How to use the app. For the API, see [API.md](API.md); for running/operating it,
[OPERATIONS.md](OPERATIONS.md); for how it's built, [ARCHITECTURE.md](ARCHITECTURE.md).

**Live app:** https://ap-ui-prod.onrender.com/

## Signing in

The app is role-based — sign in determines what you see. Demo accounts:

| Role | Email | Password | Sees |
|---|---|---|---|
| Clerk | `priya@zamp.ai` | `demo-clerk-1` | Run view, Batch ingest, Processed, Review queue |
| Clerk | `rahul@zamp.ai` | `demo-clerk-2` | (same) |
| Manager | `anjali@zamp.ai` | `demo-mgr-1` | Review queue, Processed, Dashboard, Policy |
| Manager | `vikram@zamp.ai` | `demo-mgr-2` | (same) |

A clerk works invoices and the queue; a manager additionally sees the dashboard and
controls policy. The sidebar only shows the pages your role can use.

## What a decision means

Every invoice gets one of three verdicts, with a reason and the evidence behind it:

- **APPROVE** — all checks passed, confidence is high, and the amount is within the
  auto-approve ceiling. Straight-through; the matched PO is drawn down.
- **FLAG** — needs a human. Lands in the Review queue with the specific reason
  (amount over the ceiling, line items don't reconcile, low extraction confidence, no
  tax on the invoice, …).
- **REJECT** — fails a hard rule (unapproved vendor, PO not found or closed, duplicate).

The decision is produced by a deterministic engine from seven checks — extraction is
the only step that uses the model, so verdicts are reproducible and explainable.

## The pages

**Run view (clerk).** Upload one invoice PDF and watch it go through the pipeline: the
stages light up from the real governance events, then a decision card shows the verdict,
the verbatim reason, the seven checks, and the source PDF. The starting point for a
single invoice.

**Batch ingest (clerk).** Upload several PDFs at once → each runs through the same
pipeline → a results table with the verdict per file. This is how a batch enters the
deployed app (in production, invoices land in an S3 bucket by date and a worker sweeps
them — see ARCHITECTURE.md).

**Review queue (clerk + manager).** Everything flagged for a human, oldest first. Each
flag type gets the view the reviewer needs — line items side-by-side with the PO,
amount vs. the approval ceiling, extracted fields next to the scan, or the missing-tax
notice — plus the source PDF. Approve, reject, or escalate with a note; the action is
recorded on the audit trail.

**Processed (clerk + manager).** Every decision the system has made (a clerk sees their
own, a manager sees all). Open one to review it or **manually reject (override)** an
auto-decision; the override is recorded with the actor.

**Dashboard (manager).** The operational picture: straight-through rate, cycle time,
flags and rejections by reason, a 30-day trend, and a runs table. Click a run for its
full **audit trail** — who decided what, when. A **⚠️ Demo controls** section resets the
demo data (below).

**Policy (manager).** Edit the auto-approve ceiling and confidence gate. Policy is data,
not code — the next invoice respects the change with no redeploy.

## Demo walkthrough (~5 minutes)

The app starts on a **clean slate** — the demo builds everything up live. Generate the
demo invoices first: `python scripts/make_demo_invoices.py` (writes `data/demo/batch/`
and `data/demo/edges/`).

1. **Clerk → Batch ingest.** Upload the five files in `data/demo/batch/`. The table comes
   back **all straight-through**: three APPROVE (within PO and ceiling) and two REJECT
   (an unapproved vendor and a closed PO). No human needed — that's the volume the team
   stops touching.
2. **Clerk → Run view, the edge cases one at a time, from `data/demo/edges/`:**
   - **TechGear** → **FLAG**: amount exceeds the auto-approve ceiling.
   - **FastFreight** → **FLAG**: the invoice shows no tax.
   - **Dell** → **FLAG**: the total matches the PO but the line items don't reconcile
     (the side-by-side shows the substitution).
   - **GreenLeaf** is a **scanned image** → it runs the vision path, reads cleanly at high
     confidence, and **APPROVEs** — showing the system handles scans.
   - re-upload any batch file → **REJECT (duplicate)** — it won't pay the same invoice twice.
3. **Manager → Review queue / Dashboard.** The three flags are now in the queue (open one
   to show the tailored review view); the dashboard KPIs and trend have filled in; open a
   run for its audit trail. Optionally lower the ceiling in **Policy** and re-run a fresh
   invoice to show the verdict change.

**Reset between runs:** Manager → Dashboard → **⚠️ Demo controls → Reset demo data**.
This clears processed runs and restores every PO to its baseline — a clean slate for the
next take. (It's gated by `ALLOW_DEMO_RESET`, set on the demo deployment only.)

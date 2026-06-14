# Deployment & CI/CD

How code gets from a branch to a running Render environment. For the manual
first-time Render setup see [OPERATIONS.md §3](OPERATIONS.md); for the demo, see
[DEMO.md](DEMO.md).

## Branch strategy

```
feature/* ──PR──▶ develop ──PR──▶ staging ──PR──▶ production
                    │               │                │
                    │               ▼                ▼
                    │          Render staging   Render production
                    ▼
               CI only (no deploy)
```

- **`develop`** — integration branch (the repo default). Every PR runs CI; merging
  does **not** deploy.
- **`staging`** — pre-prod. Merging a PR here runs CI, then deploys the **staging**
  Render services. Use it to rehearse before showing production.
- **`production`** — live. Merging a PR here runs CI, then deploys the **production**
  Render services (the URL you submit / demo).

Promote by PR: `develop → staging → production`. Each hop re-runs the suite, so nothing
reaches production without passing tests.

## Pipelines (GitHub Actions)

| Workflow | Trigger | Does |
|---|---|---|
| [`ci.yml`](../.github/workflows/ci.yml) | every PR; push to `develop`; called by deploy | Spins a Postgres service, seeds schema + reference data + users, runs `pytest`, runs the `validate_all --dry-run` verdict-matrix smoke, and builds the deploy Docker image |
| [`deploy.yml`](../.github/workflows/deploy.yml) | push to `staging` / `production`; manual | **Re-runs CI** (`uses: ci.yml`), then — only if green — POSTs the matching Render **deploy hook(s)** |

CI runs with **no `ANTHROPIC_API_KEY`**, so the live-model tests skip cleanly and CI
stays free and deterministic. It runs on Python **3.12** to match the deploy image.

## One-time setup to make CD live

CD is wired but inert until you add the secrets — `deploy.yml` logs a warning and
skips (it never fails) when a hook is missing, so the PR is green meanwhile.

**1. Create the Render environments — one Blueprint does both.** Render → **New →
   Blueprint** → pick this repo. [`render.yaml`](../render.yaml) defines **both**
   environments in one apply: two databases (`ap-invoices-db-staging`,
   `ap-invoices-db-prod`) and four web services — `ap-api-staging` / `ap-ui-staging`
   (pinned to the `staging` branch) and `ap-api` / `ap-ui` (pinned to `production`).
   Each service has `autoDeploy: false`, so a raw push never deploys.
   - On each `*-api` service set `ANTHROPIC_API_KEY` (secret).
   - After the first deploy, set each `*-ui` service's `API_BASE_URL` to its sibling
     api URL (e.g. `https://ap-api-staging.onrender.com`).
   - (For a single-env demo, delete the staging blocks from `render.yaml` and wire only
     production. If your account allows just one free DB, share one — see the note in
     `render.yaml`.)

**2. Auto-deploy is already OFF** (`autoDeploy: false` in the Blueprint). That hands
   the deploy decision to GitHub Actions, so **CI gates every release** instead of
   Render deploying on raw push. (If you created services by hand instead, set
   Settings → Build & Deploy → Auto-Deploy → No.)

**3. Copy each service's Deploy Hook** (Settings → Deploy Hook — a secret URL) into
   GitHub repo **Settings → Secrets and variables → Actions**:

   | Secret | From |
   |---|---|
   | `RENDER_DEPLOY_HOOK_PRODUCTION_API` | `ap-api` deploy hook |
   | `RENDER_DEPLOY_HOOK_PRODUCTION_UI`  | `ap-ui` deploy hook |
   | `RENDER_DEPLOY_HOOK_STAGING_API`    | `ap-api-staging` deploy hook |
   | `RENDER_DEPLOY_HOOK_STAGING_UI`     | `ap-ui-staging` deploy hook |

   Set only the ones you use; the rest stay skipped.

**4. (Recommended) Protect the branches.** GitHub → Settings → Branches: require the
   **CI** check to pass before merging into `staging` / `production`, and require a PR.
   Optionally add a required reviewer on the `production` GitHub Environment for a
   manual approval gate before the production deploy step runs.

## What a release looks like

1. Merge a PR into `staging` → `deploy.yml` runs CI → on green, POSTs the staging
   hooks → Render rebuilds `ap-*-staging` from the `staging` branch.
2. Verify on the staging URL.
3. Open a PR `staging → production`, merge → same flow against production.
4. Roll back by re-running `deploy.yml` (Actions → Deploy → Run workflow) on the last
   good commit, or via Render's "Rollback" on the service.

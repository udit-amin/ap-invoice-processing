# Deployment & CI/CD

How code gets from a branch to a running Render environment. For the manual
first-time Render setup see [OPERATIONS.md §3](OPERATIONS.md); for the demo, see
[DEMO.md](DEMO.md).

## Branch strategy

```
feature/* ──PR──▶ main ──PR──▶ staging ──PR──▶ production
                   │             │                │
                   │             ▼                ▼
                   │        Render staging   Render production
                   ▼
              CI only (no deploy)
```

- **`main`** — integration branch. Every PR runs CI; merging does **not** deploy.
- **`staging`** — pre-prod. Merging a PR here runs CI, then deploys the **staging**
  Render services. Use it to rehearse before showing production.
- **`production`** — live. Merging a PR here runs CI, then deploys the **production**
  Render services (the URL you submit / demo).

Promote by PR: `main → staging → production`. Each hop re-runs the suite, so nothing
reaches production without passing tests.

## Pipelines (GitHub Actions)

| Workflow | Trigger | Does |
|---|---|---|
| [`ci.yml`](../.github/workflows/ci.yml) | every PR; push to `main`; called by deploy | Spins a Postgres service, seeds schema + reference data + users, runs `pytest`, runs the `validate_all --dry-run` verdict-matrix smoke, and builds the deploy Docker image |
| [`deploy.yml`](../.github/workflows/deploy.yml) | push to `staging` / `production`; manual | **Re-runs CI** (`uses: ci.yml`), then — only if green — POSTs the matching Render **deploy hook(s)** |

CI runs with **no `ANTHROPIC_API_KEY`**, so the live-model tests skip cleanly and CI
stays free and deterministic. It runs on Python **3.12** to match the deploy image.

## One-time setup to make CD live

CD is wired but inert until you add the secrets — `deploy.yml` logs a warning and
skips (it never fails) when a hook is missing, so the PR is green meanwhile.

**1. Create the Render environments.** The simplest path is two service sets:
   - **production:** the [`render.yaml`](../render.yaml) Blueprint, with its services'
     branch set to `production`.
   - **staging:** a second set of services (e.g. `ap-api-staging`, `ap-ui-staging`,
     `ap-invoices-db-staging`) connected to the `staging` branch. (For a single-env
     demo you can skip staging and only wire production.)

**2. Turn OFF Render auto-deploy** on each service (Settings → Build & Deploy →
   Auto-Deploy → No). This hands the deploy decision to GitHub Actions, so **CI gates
   every release** instead of Render deploying on raw push.

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

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

## First-time hosting on Render (detailed)

One Blueprint provisions **both** environments. The resources (from
[`render.yaml`](../render.yaml)):

| | Staging (branch `staging`) | Production (branch `production`) |
|---|---|---|
| Database | `ap-invoices-db-staging` | `ap-invoices-db-prod` |
| API | `ap-api-staging` | `ap-api-prod` |
| UI | `ap-ui-staging` | `ap-ui-prod` |

CD is wired but inert until the hook secrets exist — `deploy.yml` logs a warning and
skips (never fails) when a hook is missing, so PRs stay green meanwhile.

**1. Apply the Blueprint.** Render → **New → Blueprint** → connect GitHub → pick this
   repo. Render reads `render.yaml` from the default branch (`develop`) and lists the 2
   databases + 4 web services. Click **Apply**. First build takes a few minutes (it
   builds the Docker image per service). Each `*-api` self-applies the schema + seeds
   reference data + the 4 demo users on first boot.

**2. Set the API secret.** On **`ap-api-staging`** and **`ap-api-prod`** → Environment →
   add `ANTHROPIC_API_KEY` = your key (it's `sync: false`, so it's never in git). Save
   (each redeploys).

**3. Wire each UI to its API.** Copy each API service's URL from its Render page, then on
   the matching UI service → Environment → set `API_BASE_URL` (full `https://`, no
   trailing slash):
   - `ap-ui-staging` → `https://ap-api-staging.onrender.com`
   - `ap-ui-prod`    → `https://ap-api-prod.onrender.com`
   (Exact host may carry a random suffix if the name was taken — copy the real one.)

**4. Seed demo history per environment.** Free-tier services have no Shell, so run it
   from your laptop against each database's **External URL** (Render → the db → Connect):
   ```bash
   DATABASE_URL='<staging-external-url>?sslmode=require' .venv/bin/python scripts/seed_demo_history.py
   DATABASE_URL='<prod-external-url>?sslmode=require'    .venv/bin/python scripts/seed_demo_history.py
   ```

**5. Copy each Deploy Hook → GitHub secret.** On each service: Settings → **Deploy Hook**
   (a secret URL). Add to GitHub repo **Settings → Secrets and variables → Actions**:

   | GitHub secret | From service |
   |---|---|
   | `RENDER_DEPLOY_HOOK_STAGING_API`    | `ap-api-staging` |
   | `RENDER_DEPLOY_HOOK_STAGING_UI`     | `ap-ui-staging` |
   | `RENDER_DEPLOY_HOOK_PRODUCTION_API` | `ap-api-prod` |
   | `RENDER_DEPLOY_HOOK_PRODUCTION_UI`  | `ap-ui-prod` |

   Auto-deploy is already **off** (`autoDeploy: false`), so deploys happen only through
   these CI-gated hooks. Set only the ones you use; the rest stay skipped.

**6. (Recommended) Protect the branches.** GitHub → Settings → Branches: require the
   **CI** check + a PR before merging into `staging` / `production`. Optionally add a
   required reviewer on the `production` GitHub Environment for a manual approval gate.

**Notes**
- For a single-env demo, delete the `staging` blocks from `render.yaml` and wire only
  production.
- If your account allows only one free PostgreSQL, drop one db block and point both
  envs at the survivor (see the note in `render.yaml`).

## What a release looks like

1. Merge a PR into `staging` → `deploy.yml` runs CI → on green, POSTs the staging hooks
   → Render rebuilds `ap-*-staging` from the `staging` branch.
2. Verify on the `ap-ui-staging` URL.
3. Open a PR `staging → production`, merge → same flow against `ap-*-prod`.
4. Roll back by re-running `deploy.yml` (Actions → Deploy → Run workflow) on the last
   good commit, or via Render's "Rollback" on the service.

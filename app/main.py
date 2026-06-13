"""FastAPI application factory for the AP invoice processor (v3).

Wires the auth, pipeline, and audit routers and, on startup, makes the service
self-bootstrapping: it applies the schema and seeds reference data + the demo
users so `docker compose up` (or `uvicorn app.main:app`) yields a working system
with no extra steps. Seeding is best-effort — a transient DB outage logs a
warning rather than crashing the process, and /health still answers.

Start the server:
    uvicorn app.main:app --reload
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import config

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: migrate (apply schema) → seed reference data → seed demo users."""
    try:
        from app.db import seed as db_seed
        from app.users.seed import seed_users

        db_seed.seed()          # apply_schema() + vendors / POs / policy
        seed_users()            # idempotent upsert of the four demo users
        log.info("startup: schema applied, reference data + demo users seeded")
    except Exception as exc:  # noqa: BLE001 — never block startup on seeding
        log.warning("startup seeding skipped/failed (DB unavailable?): %s", exc)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="AP Invoice Processing API",
        description="Upload, validate, and adjudicate PDF invoices with role-based access.",
        version="0.3.0",
        lifespan=lifespan,
    )

    # Imported here to keep app construction free of import-time side effects.
    from app.auth.router import router as auth_router
    from app.audit.router import router as audit_router
    from app.pipeline.router import router as pipeline_router
    from app.invoices.router import router as invoices_router
    from app.review.router import router as review_router
    from app.dashboard.router import router as dashboard_router
    from app.policy.router import router as policy_router

    app.include_router(auth_router)
    app.include_router(pipeline_router)
    app.include_router(invoices_router)
    app.include_router(review_router)
    app.include_router(dashboard_router)
    app.include_router(audit_router)
    app.include_router(policy_router)

    @app.get("/health", tags=["meta"])
    def health():
        """Liveness probe (ALB target-group health check)."""
        return {
            "status": "ok",
            "model": config.MODEL,
            "api_key_set": config.get_api_key() is not None,
        }

    return app


app = create_app()

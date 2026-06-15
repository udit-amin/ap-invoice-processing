"""Central configuration — constants and path resolution only.

No secrets are stored here. The Anthropic API key is read from the environment
at call time via get_api_key(). Governance values (auto-approve ceiling,
per-PO tolerance) live in the policy_config DB table, not here.
"""
from __future__ import annotations

import os
from pathlib import Path

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096

# PDFs with fewer than this many characters per page are treated as image-only
# and routed to the Claude vision path.
SCANNED_CHAR_THRESHOLD = 20

# Confidence below this threshold is flagged in API responses. The "route to
# human review" logic that acts on it comes in a later version.
CONFIDENCE_THRESHOLD = 0.80

# DPI for rasterising PDFs (vision path + image-only invoice generation).
RASTER_DPI = 200

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
INPUTS_DIR = DATA_DIR / "inputs"
SCHEMA_PATH = PROJECT_ROOT / "app" / "db" / "schema.sql"

# Default DSN matches docker-compose.yml. Override via DATABASE_URL (env or .env).
DEFAULT_DATABASE_URL = "postgresql://ap:ap@localhost:5432/ap_invoices"


def _read_env_value(key: str) -> str | None:
    """Read KEY from the environment, falling back to an untracked .env file."""
    val = os.environ.get(key)
    if val:
        return val
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        prefix = f"{key}="
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith(prefix) and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def get_api_key() -> str | None:
    """Return the Anthropic API key from the environment or an untracked .env file."""
    return _read_env_value("ANTHROPIC_API_KEY")


def get_database_url() -> str:
    """Return the Postgres DSN from the environment/.env, or the compose default."""
    return _read_env_value("DATABASE_URL") or DEFAULT_DATABASE_URL


# --------------------------------------------------------------------------- #
# Environment / deployment (v3)
# --------------------------------------------------------------------------- #
def get_environment() -> str:
    """'production' or 'development'.

    Defaults to development so local dev and the test suite run with no extra
    setup; the container/AWS task sets ENVIRONMENT=production explicitly.
    """
    return (_read_env_value("ENVIRONMENT") or "development").lower()


def demo_reset_enabled() -> bool:
    """Whether the destructive `POST /admin/reset-demo` endpoint is available.

    Off by default (a real deployment never wants a one-click data wipe); the
    demo Render services set ALLOW_DEMO_RESET=true to expose the reset button.
    """
    return (_read_env_value("ALLOW_DEMO_RESET") or "").strip().lower() in ("1", "true", "yes")


# --------------------------------------------------------------------------- #
# Auth / JWT (v3)
# --------------------------------------------------------------------------- #
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_SECONDS = int(_read_env_value("JWT_EXPIRE_SECONDS") or 3600)

# Clearly-insecure dev fallback. get_jwt_secret() refuses to use it in
# production — there a missing JWT_SECRET is fatal rather than silently signing
# tokens with a public key. In development it keeps local runs/tests zero-setup.
_DEV_JWT_SECRET = "dev-insecure-jwt-secret-change-me"


def get_jwt_secret() -> str:
    """Return the HS256 signing secret from the environment/.env.

    Fatal in production if unset; falls back to a dev-only key otherwise.
    """
    secret = _read_env_value("JWT_SECRET")
    if secret:
        return secret
    if get_environment() == "production":
        raise RuntimeError("JWT_SECRET must be set in production")
    return _DEV_JWT_SECRET


# --------------------------------------------------------------------------- #
# Multi-tenancy hook (v3, §3.4) — one constant tenant for now. The discipline
# is filtering by tenant_id, not dynamic tenancy yet. TENANT_ID must match the
# column DEFAULT in app/db/schema.sql; it is uuid5(NAMESPACE_DNS, TENANT_LABEL).
# --------------------------------------------------------------------------- #
TENANT_LABEL = "acme-corp-001"
TENANT_ID = "11fbb063-9253-5c06-8412-f2aa4bb88084"

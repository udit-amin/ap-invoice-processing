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
DB_PATH = DATA_DIR / "invoices.db"
SCHEMA_PATH = PROJECT_ROOT / "src" / "db" / "schema.sql"


def get_api_key() -> str | None:
    """Return the Anthropic API key from the environment or an untracked .env file."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None

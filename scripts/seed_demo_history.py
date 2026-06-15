"""Reset the demo to a clean slate — clear all processed runs and restore reference
data (vendors, POs, policy) to baseline. The demo then builds everything up live (a
straight-through batch, then the edge cases one at a time).

Thin CLI wrapper over the single source of truth, `app.admin.service.reset_demo_data`
(the same logic behind the in-app "Reset demo data" button). Demo-only; no model calls.

    .venv/bin/python scripts/seed_demo_history.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.admin.service import reset_demo_data  # noqa: E402


def main() -> None:
    reset_demo_data()
    print("Demo reset — processed runs cleared, reference data restored (clean slate).")


if __name__ == "__main__":
    main()

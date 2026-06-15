"""Seed a few days of back-dated runs so the dashboard's trend and KPIs aren't
empty during a demo, and store each run's source PDF + extraction so the review
queue's flag-type views (incl. the low-confidence scan) work offline.

Thin CLI wrapper over the single source of truth, `app.admin.service.reset_demo_data`
(the same logic behind the in-app "Reset demo data" button). Demo-only; reuses the
answer-key pipeline (no model calls); verdict mix stays 6 APPROVE / 3 FLAG / 2 REJECT.

    .venv/bin/python scripts/seed_demo_history.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.admin.service import reset_demo_data  # noqa: E402


def main() -> None:
    result = reset_demo_data()
    print(f"Seeded {result['runs']} runs across {result['days']} days: {result['tally']}")


if __name__ == "__main__":
    main()

"""Tiny display formatters shared by the views (presentation only)."""
from __future__ import annotations

from datetime import datetime


def short_ts(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%d %b %H:%M")
    except (ValueError, AttributeError):
        return iso


def rupees(n) -> str:
    return f"₹{n:,.0f}" if isinstance(n, (int, float)) else "—"


def pct(x, digits: int = 0) -> str:
    return f"{x:.{digits}%}" if isinstance(x, (int, float)) else "—"

"""The live run-view stage indicator: the seven human stages as a vertical list,
each with a state icon. Re-rendered into a placeholder as the real events replay.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

_ICON = {
    "pending": "⚪️",
    "running": "🔄",
    "done":    "✅",
    "fail":    "❌",
    "skipped": "➖",
}


def render(stages: list[dict[str, str]], into: Any | None = None) -> None:
    """Render the stage list. Pass `into` (an st.empty()) to replace in place
    during the live replay; omit it to render once."""
    target = into if into is not None else st
    lines = []
    for s in stages:
        icon = _ICON.get(s.get("status"), "⚪️")
        label = s.get("label", "")
        if s.get("status") in (None, "pending"):
            lines.append(f"{icon}&nbsp;&nbsp;:gray[{label}]")
        else:
            lines.append(f"{icon}&nbsp;&nbsp;**{label}**")
    target.markdown("\n\n".join(lines), unsafe_allow_html=True)

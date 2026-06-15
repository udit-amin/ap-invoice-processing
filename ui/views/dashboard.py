"""Dashboard (manager) — four bands: headline KPI cards, exception/rejection
breakdowns, the 30-day trend, and the filterable runs table with an audit
drill-in. Read-only API calls are cached briefly; the queue is never cached.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import api_client
import fmt
from components import decision_card

_FLAG_LABELS = {
    "line_variance": "Line variance", "over_authority": "Over authority",
    "tolerance": "Over tolerance", "low_confidence": "Low confidence",
    "incomplete": "Incomplete", "other": "Other",
}
_REJECT_LABELS = {
    "duplicate": "Duplicate", "unapproved_vendor": "Unapproved vendor",
    "closed_po": "Closed/maxed PO", "po_not_found": "PO not found",
}


@st.cache_data(ttl=30)
def _kpis(days: int):
    return api_client.get_kpis(days)


@st.cache_data(ttl=30)
def _trends(days: int):
    return api_client.get_trends(days)


@st.cache_data(ttl=30)
def _runs(verdict: str | None):
    return api_client.get_runs(verdict=verdict, limit=200)


def render() -> None:
    st.title("📊 Dashboard")
    try:
        k = _kpis(30)
    except api_client.ApiError as exc:
        st.error(exc.friendly())
        return

    _band_kpis(k)
    st.divider()
    _band_breakdowns(k)
    st.divider()
    _band_trend()
    st.divider()
    _band_runs()
    st.divider()
    _demo_controls()


def _delta_pts(d):
    return f"{d * 100:+.1f} pts" if isinstance(d, (int, float)) else None


def _delta_num(d, prefix="", suffix=""):
    return f"{prefix}{d:+,.0f}{suffix}" if isinstance(d, (int, float)) else None


def _band_kpis(k: dict) -> None:
    kp = k["kpis"]
    r1 = st.columns(3)
    r1[0].metric("Straight-through rate", fmt.pct(kp["stp_rate"]["value"]),
                 _delta_pts(kp["stp_rate"]["delta"]))
    cyc = kp["avg_cycle_ms"]["value"]
    r1[1].metric("Avg cycle time", f"{cyc:.0f} ms" if cyc is not None else "—",
                 _delta_num(kp["avg_cycle_ms"]["delta"], suffix=" ms"), delta_color="inverse")
    tiq = kp["avg_time_in_queue_sec"]["value"]
    r1[2].metric("Avg time in queue", _dur(tiq))

    r2 = st.columns(3)
    r2[0].metric("Touchless savings", fmt.rupees(kp["touchless_savings"]["value"]),
                 _delta_num(kp["touchless_savings"]["delta"], prefix="₹"))
    r2[1].metric("Audit completeness", fmt.pct(kp["audit_completeness"]["value"]))
    # Quality monitoring is shown honestly rather than faked — false-approve and
    # override rates need a downstream ground-truth signal the demo doesn't have.
    with r2[2]:
        st.markdown("**Quality monitoring**")
        st.caption("ℹ️ False-approve / override rates — coming once audit feedback "
                   "is connected.")


def _dur(seconds) -> str:
    if not isinstance(seconds, (int, float)):
        return "—"
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def _bars(counts: dict, labels: dict) -> None:
    counts = {k: v for k, v in (counts or {}).items() if v}
    if not counts:
        st.caption("None in this period.")
        return
    df = pd.DataFrame({"count": list(counts.values())},
                      index=[labels.get(k, k) for k in counts])
    st.bar_chart(df, horizontal=True)


def _band_breakdowns(k: dict) -> None:
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Flags by reason**")
        _bars(k.get("flags_by_reason"), _FLAG_LABELS)
    with c2:
        st.markdown("**Rejections by reason**")
        _bars(k.get("rejections_by_reason"), _REJECT_LABELS)


def _band_trend() -> None:
    st.markdown("**Last 30 days**")
    try:
        rows = _trends(30).get("trends") or []
    except api_client.ApiError as exc:
        st.error(exc.friendly())
        return
    if not rows:
        st.caption("No history yet — seed a few days of runs to populate this.")
        return
    df = pd.DataFrame(rows)[["date", "APPROVE", "FLAG", "REJECT"]].set_index("date")
    st.bar_chart(df, color=["#21c354", "#ffa421", "#ff4b4b"])


def _band_runs() -> None:
    st.markdown("**Runs**")
    choice = st.selectbox("Filter by verdict", ["All", "APPROVE", "FLAG", "REJECT"])
    try:
        runs = _runs(None if choice == "All" else choice).get("runs") or []
    except api_client.ApiError as exc:
        st.error(exc.friendly())
        return
    if not runs:
        st.caption("No runs.")
        return

    df = pd.DataFrame([{
        "Invoice": r.get("invoice_number"),
        "Vendor": r.get("vendor_name"),
        "Amount": r.get("invoice_total"),
        "Verdict": r.get("verdict"),
        "Confidence": r.get("overall_conf"),
        "Who": r.get("actor_role") or "system",
        "When": fmt.short_ts(r.get("started_at")),
    } for r in runs])

    event = st.dataframe(
        df, hide_index=True, use_container_width=True,
        on_select="rerun", selection_mode="single-row",
        column_config={
            "Amount": st.column_config.NumberColumn(format="₹%.0f"),
            "Confidence": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    rows = event.selection.rows if event and event.selection else []
    if rows:
        inv = runs[rows[0]].get("invoice_number")
        if inv:
            st.divider()
            _audit(inv)


def _demo_controls() -> None:
    with st.expander("⚠️ Demo controls"):
        st.caption("Clears all processed runs and restores every PO to its baseline — a "
                   "clean slate between demo takes. Reference data (vendors, POs, policy, "
                   "users) is preserved.")
        confirm = st.checkbox("I understand this clears all processed invoices", key="reset_confirm")
        if st.button("🔄 Reset demo data", type="primary", disabled=not confirm):
            try:
                api_client.reset_demo()
            except api_client.ApiError as exc:
                st.error(exc.friendly())
                return
            st.cache_data.clear()  # KPIs/trends/runs are cached — refresh them
            st.toast("Demo data reset — clean slate.", icon="🔄")
            st.rerun()


def _audit(invoice_number: str) -> None:
    st.markdown(f"### Audit trail — {invoice_number}")
    try:
        trail = api_client.get_audit(invoice_number)
    except api_client.ApiError as exc:
        st.error(exc.friendly())
        return
    if trail.get("latest_verdict"):
        decision_card.render(trail["latest_verdict"])
    for run in trail.get("runs") or []:
        st.caption(f"Run {run['run_id'][:8]} · started {fmt.short_ts(run.get('started_at'))}")
        for e in run.get("events") or []:
            who = e.get("actor") or "system"
            role = f" ({e['actor_role']})" if e.get("actor_role") else ""
            st.markdown(f"- `{e.get('stage')}` · {e.get('status')} · "
                        f"**{who}**{role} · {fmt.short_ts(e.get('ts'))}")

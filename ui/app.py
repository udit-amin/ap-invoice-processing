"""AP Invoice Processing — Streamlit front end.

A thin client over the v3 API: login gate, then role-driven navigation. The
sidebar shows only the pages the role can use (no render-then-403). All data
comes from api_client; this app contains no business logic.

Run:  API_BASE_URL=http://localhost:8000 streamlit run ui/app.py
"""
from __future__ import annotations

import streamlit as st

import api_client
import session
from views import dashboard, policy, review_queue, run_view

st.set_page_config(page_title="AP Invoice Processing", page_icon="🧾", layout="wide")


def _login() -> None:
    st.title("🧾 AP Invoice Processing")
    st.caption("Sign in to continue.")
    with st.form("login"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")
    if submitted:
        try:
            res = api_client.login(email.strip(), password)
        except api_client.ApiError as exc:
            st.error("Invalid email or password." if exc.status == 401 else exc.friendly())
            return
        session.login(res["access_token"], res.get("user") or {})
        st.rerun()

    with st.expander("Demo users"):
        st.markdown(
            "- **Priya / Rahul** — clerk · `priya@acmecorp.com` / `rahul@acmecorp.com` · `demo-clerk-1` / `demo-clerk-2`\n"
            "- **Anjali / Vikram** — manager · `anjali@acmecorp.com` / `vikram@acmecorp.com` · `demo-mgr-1` / `demo-mgr-2`"
        )


def _sidebar() -> None:
    with st.sidebar:
        st.markdown("### 🧾 AP Invoices")
        st.markdown(f"**{session.name() or 'User'}**")
        st.caption(f"Role: {session.role() or '—'}")
        if st.button("Log out", use_container_width=True):
            session.logout()
            st.rerun()
        st.divider()
        st.caption(f"API: {api_client.API_BASE_URL}")


# Pages each role may use — the nav renders only these (handover §3.2).
# Explicit url_path per page: every view function is named render(), so the
# pathname can't be inferred uniquely from the callable name.
_PAGES = {
    "clerk": lambda: [
        st.Page(run_view.render, title="Run view", icon="📤", url_path="run", default=True),
        st.Page(review_queue.render, title="Review queue", icon="📋", url_path="review"),
    ],
    "manager": lambda: [
        st.Page(review_queue.render, title="Review queue", icon="📋", url_path="review", default=True),
        st.Page(dashboard.render, title="Dashboard", icon="📊", url_path="dashboard"),
        st.Page(policy.render, title="Policy", icon="⚙️", url_path="policy"),
    ],
}


def _main() -> None:
    _sidebar()
    pages = _PAGES.get(session.role())
    if pages is None:
        st.error(f"Unknown role: {session.role()!r}. Please log out and back in.")
        return
    st.navigation(pages()).run()


if session.is_authed():
    _main()
else:
    _login()

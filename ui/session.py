"""Session state: the bearer token + current user, kept in st.session_state so
they survive Streamlit's top-to-bottom rerun on every interaction.

This is the only module that touches st.session_state for auth; pages read the
current user/role through these helpers.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

_TOKEN = "token"
_USER = "user"


def is_authed() -> bool:
    return bool(st.session_state.get(_TOKEN))


def login(token: str, user: dict[str, Any]) -> None:
    st.session_state[_TOKEN] = token
    st.session_state[_USER] = user


def logout() -> None:
    for key in (_TOKEN, _USER):
        st.session_state.pop(key, None)


def token() -> str | None:
    return st.session_state.get(_TOKEN)


def user() -> dict[str, Any]:
    return st.session_state.get(_USER) or {}


def role() -> str | None:
    return user().get("role")


def name() -> str | None:
    return user().get("name")

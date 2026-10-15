"""Streamlit entry point. Run with ``dea ui`` or ``streamlit run src/dea/ui/app.py``."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

st.set_page_config(
    page_title="Diagnostic Expert Advisor",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="expanded",
)

PAGES = Path(__file__).parent / "pages"

nav = st.navigation(
    [
        st.Page(PAGES / "home.py", title="Cohorts", icon=":material/folder_open:", default=True),
        st.Page(PAGES / "overview.py", title="Overview", icon=":material/dashboard:"),
        st.Page(PAGES / "encounters.py", title="Encounters", icon=":material/person_search:"),
        st.Page(PAGES / "profile.py", title="Profile & Advice", icon=":material/insights:"),
        st.Page(PAGES / "compute.py", title="Compute", icon=":material/memory:"),
    ]
)
nav.run()

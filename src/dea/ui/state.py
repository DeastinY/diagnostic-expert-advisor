"""Session state helpers shared by all pages."""

from __future__ import annotations

import os
from pathlib import Path

import polars as pl
import streamlit as st

from dea.core import Cohort
from dea.io import list_cohorts, load_cohort

DEFAULT_DATA_DIR = Path(os.environ.get("DEA_DATA_DIR", "data")).resolve()


def data_dir() -> Path:
    path = st.session_state.get("data_dir")
    if path is None:
        path = DEFAULT_DATA_DIR
        st.session_state["data_dir"] = path
    return Path(path)


def set_data_dir(path: Path | str) -> None:
    st.session_state["data_dir"] = Path(path).resolve()


def _dir_signature(path: Path) -> float:
    """Changes whenever the cohort on disk changes, so caches refresh after a save."""
    files = [path / "cohort.yaml", path / "static.parquet", *(path / "dynamic").rglob("*.parquet")]
    return max((f.stat().st_mtime for f in files if f.exists()), default=0.0)


@st.cache_resource(show_spinner="Loading cohort…")
def _load(path: str, signature: float) -> Cohort:
    return load_cohort(path)


def set_cohort(path: Path | str) -> None:
    st.session_state["cohort_path"] = str(Path(path).resolve())
    st.session_state["active_filters"] = []
    st.session_state.pop("profile", None)
    st.session_state.pop("recommendation", None)
    st.session_state.pop("selected_encounter", None)


def cohort_path() -> Path | None:
    p = st.session_state.get("cohort_path")
    return Path(p) if p else None


def cohort() -> Cohort | None:
    """The full loaded cohort, or None."""
    path = cohort_path()
    if path is None or not path.exists():
        return None
    return _load(str(path), _dir_signature(path))


def invalidate() -> None:
    """Forget cached cohort objects (after writing config or derived data)."""
    _load.clear()
    st.session_state.pop("profile", None)


def active_filters() -> list[str]:
    return st.session_state.get("active_filters", [])


def filtered_cohort() -> Cohort | None:
    """The loaded cohort with the sidebar filters applied."""
    base = cohort()
    if base is None:
        return None
    labels = [f for f in active_filters() if f in {r.label for r in base.config.filters}]
    if not labels:
        return base
    try:
        return base.filter_by_labels(labels)
    except Exception as exc:  # a bad filter in cohort.yaml should not take the UI down
        st.warning(f"Filter could not be applied: {exc}")
        return base


def available_cohorts() -> list:
    return list_cohorts(data_dir())


def require_cohort() -> Cohort:
    c = filtered_cohort()
    if c is None:
        st.info("Select or import a cohort on the **Cohorts** page first.")
        st.stop()
    return c


def sidebar() -> None:
    """Common sidebar: current cohort, filters, data directory."""
    base = cohort()
    with st.sidebar:
        if base is None:
            st.caption("No cohort loaded")
        else:
            st.markdown(f"**{base.name}**")
            n_all = len(base)
            if base.config.filters:
                chosen = st.multiselect(
                    "Filters",
                    [r.label for r in base.config.filters],
                    default=active_filters(),
                    help="Defined in cohort.yaml; combined with AND",
                    key="filter_select",
                )
                if chosen != active_filters():
                    st.session_state["active_filters"] = chosen
                    st.rerun()
            shown = filtered_cohort()
            n_shown = len(shown) if shown is not None else n_all
            st.caption(f"{n_shown} of {n_all} encounters selected")
        st.divider()
        st.caption(f"Data directory: `{data_dir()}`")


def csv_bytes(frame: pl.DataFrame) -> bytes:
    """CSV export with Duration columns rendered as hours (CSV has no duration type)."""
    exprs = []
    renames = {}
    for name, dtype in frame.schema.items():
        if dtype == pl.Duration:
            exprs.append((pl.col(name).dt.total_seconds() / 3600.0).alias(name))
            renames[name] = f"{name}_hours"
    if exprs:
        frame = frame.with_columns(exprs).rename(renames)
    return frame.write_csv().encode()

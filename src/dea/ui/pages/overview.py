"""Overview page: the cohort at a glance."""

from __future__ import annotations

import polars as pl
import streamlit as st

from dea.core import ENCOUNTER_ID
from dea.core.time import format_duration
from dea.ui import charts, state

state.sidebar()
cohort = state.require_cohort()
full = state.cohort()
cfg = cohort.config

st.title(cfg.name)
if cfg.description:
    st.caption(cfg.description)
if state.active_filters():
    st.info(
        "Showing **"
        + " AND ".join(state.active_filters())
        + f"** · {len(cohort)} of {len(full)} encounters"
    )

feats = cohort.features()
summary = cohort.summary()

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Encounters", f"{summary.n_encounters:,}")
k2.metric("Variables", summary.n_variables)
k3.metric("Observations", f"{summary.n_observations:,}")
k4.metric("Median stay", format_duration(summary.los_hours_median * 3600))
k5.metric("Longest stay", format_duration(summary.los_hours_max * 3600))

if len(cohort) == 0:
    st.warning("No encounters match the current filters.")
    st.stop()

left, right = st.columns(2, gap="large")
with left:
    st.subheader("Length of stay")
    st.altair_chart(
        charts.histogram(feats["los_hours"] / 24, "Length of stay", "days"),
        width="stretch",
    )
with right:
    st.subheader("Variable coverage")
    count_cols = [c for c in feats.columns if c.endswith("__count")]
    cov = pl.DataFrame(
        {
            "variable": [c[: -len("__count")] for c in count_cols],
            "coverage": [float((feats[c].fill_null(0) > 0).mean()) * 100 for c in count_cols],
            "observations": [f"{int(feats[c].fill_null(0).sum()):,}" for c in count_cols],
        }
    )
    if cov.height:
        st.altair_chart(
            charts.horizontal_bars(
                cov,
                "variable",
                "coverage",
                "Encounters with data (%)",
                value_format=".0f",
                tooltip_extra=["observations"],
            ),
            width="stretch",
        )

if cfg.tags:
    st.subheader("Tags")
    tags = cohort.tag_matrix()
    counts = pl.DataFrame(
        {
            "tag": [r.label for r in cfg.tags],
            "encounters": [int(tags[r.label].sum()) for r in cfg.tags],
            "share": [f"{100 * tags[r.label].mean():.0f}%" for r in cfg.tags],
        }
    )
    st.altair_chart(
        charts.horizontal_bars(counts, "tag", "encounters", "Encounters", tooltip_extra=["share"]),
        width="stretch",
    )

st.subheader("Explore")
e1, e2 = st.columns(2, gap="large")
with e1:
    static_cols = cohort.static_columns
    if static_cols:
        col = st.selectbox("Static column", static_cols)
        series = cohort.static[col]
        if series.dtype.is_numeric() and series.n_unique() > 12:
            st.altair_chart(charts.histogram(series, cfg.label(col)), width="stretch")
        else:
            st.altair_chart(charts.category_counts(series, cfg.label(col)), width="stretch")
    else:
        st.caption("No static columns in this cohort.")
with e2:
    variables = cohort.variables
    if variables:
        var = st.selectbox(
            "Variable (per-encounter mean)",
            variables,
            index=variables.index(cfg.plots[0]) if cfg.plots and cfg.plots[0] in variables else 0,
            format_func=cfg.label,
        )
        mean_col = f"{var}__mean"
        by_options = ["(none)"] + [
            c
            for c in cohort.static_columns
            if not cohort.static[c].dtype.is_float() and cohort.static[c].n_unique() <= 8
        ]
        by = st.selectbox("Split by", by_options)
        unit = cfg.variable_units.get(var, "")
        if by == "(none)":
            st.altair_chart(
                charts.histogram(feats[mean_col], cfg.label(var), unit), width="stretch"
            )
        else:
            st.altair_chart(
                charts.faceted_histogram(
                    feats, mean_col, by, cfg.label(var) + (f" ({unit})" if unit else "")
                ),
                width="stretch",
            )

with st.expander("Static table"):
    st.dataframe(cohort.static.to_pandas(), width="stretch", hide_index=True)
    st.download_button(
        "Download static.csv",
        cohort.static.write_csv().encode(),
        file_name=f"{cfg.name}_static.csv",
        mime="text/csv",
    )
with st.expander("Encounter feature matrix (what filters and tags are evaluated on)"):
    st.caption(
        "Columns: static data, stay statistics, and `<variable>__min|max|mean|first|last|count`."
    )
    st.dataframe(feats.head(200).to_pandas(), width="stretch", hide_index=True)
    st.download_button(
        "Download features.csv",
        feats.write_csv().encode(),
        file_name=f"{cfg.name}_features.csv",
        mime="text/csv",
    )
    st.caption(f"{feats.height} rows × {feats.width} columns; sorted by {ENCOUNTER_ID}.")

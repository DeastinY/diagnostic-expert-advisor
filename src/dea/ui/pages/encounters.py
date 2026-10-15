"""Encounters page: browse the list, open one, look at its time series."""

from __future__ import annotations

import polars as pl
import streamlit as st

from dea.core import ENCOUNTER_ID
from dea.ui import charts, state

state.sidebar()
cohort = state.require_cohort()
cfg = cohort.config

st.title("Encounters")

feats = cohort.features()
table = feats.select(
    ENCOUNTER_ID,
    (pl.col("los_hours") / 24).round(1).alias("stay_days"),
    pl.col("n_observations").alias("observations"),
    *cohort.static_columns,
)
if cfg.tags:
    tags = cohort.tag_matrix()
    table = table.join(tags, on=ENCOUNTER_ID, how="left")

query = st.text_input("Search encounter id", placeholder="type part of an id…")
if query:
    table = table.filter(pl.col(ENCOUNTER_ID).str.contains(query, literal=True))
st.caption(f"{table.height} encounters")

selection = st.dataframe(
    table.to_pandas(),
    width="stretch",
    hide_index=True,
    height=min(420, 38 + 35 * max(1, table.height)),
    on_select="rerun",
    selection_mode="single-row",
    column_config={
        "stay_days": st.column_config.NumberColumn("Stay (days)", format="%.1f"),
        **{r.label: st.column_config.CheckboxColumn(r.label, disabled=True) for r in cfg.tags},
    },
)
rows = selection.selection.rows if selection and selection.selection else []
if rows:
    st.session_state["selected_encounter"] = table[ENCOUNTER_ID][rows[0]]
selected = st.session_state.get("selected_encounter")
if selected is None or selected not in cohort:
    ids = table[ENCOUNTER_ID].to_list()
    selected = (
        st.selectbox("Encounter", ids, index=0 if ids else None, key="encounter_fallback")
        if ids
        else None
    )
if selected is None:
    st.stop()

st.divider()
enc = cohort.get(selected)
st.subheader(f"Encounter {enc.id}")
cols = st.columns(min(6, max(1, len(enc.static) + 1)))
cols[0].metric("Stay", f"{enc.los_hours / 24:.1f} d")
for i, (k, v) in enumerate(list(enc.static.items())[: len(cols) - 1], start=1):
    cols[i].metric(k.replace("_", " "), v if v is not None else "–")

available = enc.variables
default_plots = [v for v in cfg.plots if v in available] or available[:6]
c1, c2 = st.columns([3, 1])
chosen = c1.multiselect(
    "Variables", available, default=default_plots, format_func=cfg.label, max_selections=12
)
grid_options = ["raw", "5m", "15m", "1h", "4h"]
default_grid = cfg.processing.resample_every or "raw"
grid = c2.selectbox(
    "Grid",
    grid_options,
    index=grid_options.index(default_grid) if default_grid in grid_options else 0,
    help="Resample to a regular grid (mean per cell) or show raw observations",
)
if chosen:
    chart = charts.small_multiples(
        enc,
        chosen,
        None if grid == "raw" else grid,
        labels=cfg.variable_labels,
        units=cfg.variable_units,
    )
    if chart is not None:
        st.altair_chart(chart, width="stretch")
else:
    st.info("Pick at least one variable.")

with st.expander("Data table & download"):
    wide = enc.wide(every=None if grid == "raw" else grid)
    st.dataframe(wide.head(500).to_pandas(), width="stretch", hide_index=True)
    st.download_button(
        "Download CSV", state.csv_bytes(wide), file_name=f"{enc.id}.csv", mime="text/csv"
    )

with st.expander("Free-form explorer (PyGWalker)"):
    try:
        import pygwalker as pyg
    except ImportError:
        st.caption(
            "Install `dea[explore]` to drag-and-drop explore this encounter in a Tableau-style UI."
        )
    else:
        st.caption("Drag variables onto the shelves. Runs entirely in your browser.")
        if st.button("Open explorer"):
            frame = (
                enc.wide(every=None if grid == "raw" else grid)
                .with_columns((pl.col("time").dt.total_seconds() / 3600).alias("hours"))
                .drop("time")
            )
            html = pyg.to_html(frame.to_pandas(), appearance="light")
            st.components.v1.html(html, height=900, scrolling=True)

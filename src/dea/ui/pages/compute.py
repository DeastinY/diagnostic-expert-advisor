"""Compute page: run per-encounter jobs through a backend and inspect derived tables."""

from __future__ import annotations

import io
import time

import streamlit as st

from dea.compute import BUILTIN_JOBS, get_backend
from dea.ui import state

state.sidebar()
cohort = state.require_cohort()
full = state.cohort()
st.title("Compute")
st.caption(
    "Run a function on every encounter. The same call runs locally today and on a SLURM "
    "cluster later; results land in the cohort's `derived/` folder."
)

left, right = st.columns([2, 3], gap="large")
with left:
    job_name = st.selectbox("Job", list(BUILTIN_JOBS), format_func=lambda k: k.replace("_", " "))
    st.code(
        f"from dea.compute import {job_name}\n"
        f"cohort.apply({job_name}, backend=..., name={job_name!r})",
        language="python",
    )
    backend_name = st.radio(
        "Backend",
        ["serial", "local", "slurm"],
        horizontal=True,
        captions=["this process", "all local cores", "submitit array job (needs a cluster)"],
    )
    kwargs: dict = {}
    if backend_name == "local":
        kwargs["max_workers"] = st.slider("Workers", 1, 16, 4)
    if backend_name == "slurm":
        kwargs["partition"] = st.text_input("Partition", value="") or None
        kwargs["account"] = st.text_input("Account", value="") or None
        kwargs["mem_gb"] = st.number_input("Memory (GB)", 1, 512, 4)
        kwargs["timeout_min"] = st.number_input("Time limit (min)", 1, 60 * 72, 60)
        st.caption("Jobs are submitted from this machine with `sbatch`; install `dea[slurm]`.")
    scope = st.radio("Encounters", ["filtered selection", "whole cohort"], horizontal=True)
    target = cohort if scope == "filtered selection" else full
    if st.button(f"Run on {len(target)} encounters", type="primary"):
        try:
            backend = get_backend(backend_name, **kwargs)
            t0 = time.perf_counter()
            with st.spinner(f"Running {job_name} on {backend_name}…"):
                result = full.apply(
                    BUILTIN_JOBS[job_name], backend=backend, name=job_name, ids=target.encounter_ids
                )
            st.success(f"{result.height:,} rows in {time.perf_counter() - t0:.1f}s")
            state.invalidate()
        except Exception as exc:
            st.error(f"Job failed: {exc}")

with right:
    st.subheader("Derived tables")
    derived = full.list_derived()
    if not derived:
        st.info("Nothing computed yet.")
    else:
        pick = st.selectbox("Table", derived)
        frame = full.derived(pick)
        if frame is not None:
            st.caption(f"{frame.height:,} rows × {frame.width} columns")
            st.dataframe(frame.head(500).to_pandas(), width="stretch", hide_index=True)
            st.download_button(
                "Download CSV", frame.write_csv().encode(), file_name=f"{pick}.csv", mime="text/csv"
            )

st.divider()
st.subheader("Export for modelling")
st.caption("A wide frame on the cohort's processing grid, ready for Polars, pandas or PyTorch.")
proc = full.config.processing
c1, c2, c3 = st.columns(3)
every = c1.text_input("Grid", value=proc.resample_every or "1h")
fill = c2.selectbox(
    "Fill",
    ["none", "forward", "interpolate", "zero"],
    index=["none", "forward", "interpolate", "zero"].index(proc.fill),
)
with_static = c3.checkbox("Include static columns", value=True)
if st.button("Build wide frame"):
    with st.spinner("Resampling…"):
        wide = cohort.wide(every=every, fill=fill, with_static=with_static)
    st.caption(f"{wide.height:,} rows × {wide.width} columns")
    st.dataframe(wide.head(200).to_pandas(), width="stretch", hide_index=True)
    buf = io.BytesIO()
    wide.write_parquet(buf)
    st.download_button("Download Parquet", buf.getvalue(), file_name=f"{cohort.name}_wide.parquet")
    st.code(
        "import dea\n"
        f"cohort = dea.load_cohort({str(full.root)!r})\n"
        f"frame = cohort.wide(every={every!r}, fill={fill!r}, with_static={with_static})\n"
        "pdf = frame.to_pandas()  # or keep working in Polars",
        language="python",
    )

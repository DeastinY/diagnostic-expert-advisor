"""Profile & Advice: deterministic profiling, processing settings, optional AI advisor."""

from __future__ import annotations

import json

import polars as pl
import streamlit as st

from dea.advise import Advisor, AdvisorError, AdvisorSettings
from dea.core import ProcessingConfig
from dea.core.time import format_duration
from dea.profile import profile_cohort
from dea.ui import charts, state

state.sidebar()
cohort = state.require_cohort()
full = state.cohort()
cfg = full.config

st.title("Profile & advice")
st.caption(
    "Everything on this page is computed from aggregate statistics. "
    "The optional advisor only ever sees the profile shown here."
)

key = ("profile", str(full.root), len(cohort), tuple(state.active_filters()))
if st.session_state.get("profile_key") != key:
    with st.spinner("Profiling…"):
        st.session_state["profile"] = profile_cohort(cohort)
        st.session_state["profile_key"] = key
profile = st.session_state["profile"]

sp = profile.sampling
k1, k2, k3, k4 = st.columns(4)
k1.metric(
    "Typical interval",
    format_duration(sp.median_interval_s) if sp.median_interval_s else "n/a",
    help="Median gap between consecutive observations of the same variable",
)
k2.metric(
    "Regular sampling",
    f"{100 * sp.regular_fraction:.0f}%" if sp.regular_fraction is not None else "n/a",
    help="Share of gaps equal to the most common gap",
)
k3.metric(
    "Suggested grid",
    sp.suggested_resample or "n/a",
    help=f"Intervals measured on {sp.sampled_encounters} sampled encounters",
)
k4.metric("Current grid", cfg.processing.resample_every or "raw")

for w in profile.warnings:
    st.warning(w, icon="⚠️")

tab_vars, tab_events, tab_static, tab_proc = st.tabs(
    ["Variables", "Events", "Static columns", "Processing"]
)

with tab_vars:
    vf = profile.variables_frame()
    if vf.height:
        shown = vf.with_columns((pl.col("coverage") * 100).round(0)).select(
            "name",
            "coverage",
            "n_observations",
            "interval",
            "min",
            "median",
            "max",
            "std",
            "n_unique",
            "flags",
        )
        st.dataframe(
            shown.to_pandas(),
            width="stretch",
            hide_index=True,
            column_config={
                "coverage": st.column_config.ProgressColumn(
                    "Coverage", min_value=0, max_value=100, format="%d%%"
                ),
                "n_observations": st.column_config.NumberColumn("Observations", format="%d"),
                "interval": "Typical interval",
                "min": st.column_config.NumberColumn(format="%.3g"),
                "median": st.column_config.NumberColumn(format="%.3g"),
                "max": st.column_config.NumberColumn(format="%.3g"),
                "std": st.column_config.NumberColumn(format="%.3g"),
            },
        )
        st.altair_chart(
            charts.horizontal_bars(
                vf.select("name", "n_observations"), "name", "n_observations", "Observations"
            ),
            width="stretch",
        )
with tab_events:
    ef = profile.events_frame()
    if ef.height:
        st.caption("String-valued observations such as ventilation modes or drug names.")
        st.dataframe(ef.to_pandas(), width="stretch", hide_index=True)
    else:
        st.caption("No string-valued observations in this cohort.")

with tab_static:
    sf = profile.static_frame()
    if sf.height:
        st.dataframe(sf.to_pandas(), width="stretch", hide_index=True)
    else:
        st.caption("No static columns.")

with tab_proc:
    st.write(
        "How observations become a regular wide frame (used by charts, "
        "exports and `cohort.wide()`)."
    )
    proc = cfg.processing
    c1, c2, c3 = st.columns(3)
    grid = c1.text_input(
        "Resample every",
        value=proc.resample_every or "",
        placeholder="e.g. 15m, 1h — empty keeps raw timestamps",
    )
    aggs = ["mean", "median", "first", "last", "min", "max"]
    agg = c2.selectbox("Aggregate", aggs, index=aggs.index(proc.aggregate))
    fills = ["none", "forward", "interpolate", "zero"]
    fill = c3.selectbox("Fill gaps", fills, index=fills.index(proc.fill))
    drops = st.multiselect(
        "Drop variables",
        full.variables,
        default=[v for v in proc.drop_variables if v in full.variables],
    )
    if st.button("Save processing settings"):
        try:
            cfg.processing = ProcessingConfig(
                resample_every=grid.strip() or None,
                aggregate=agg,
                fill=fill,
                fill_overrides=proc.fill_overrides,
                drop_variables=drops,
            )
            cfg.save(full.root / "cohort.yaml")
            state.invalidate()
            st.success("Saved to cohort.yaml")
            st.rerun()
        except Exception as exc:
            st.error(f"Invalid settings: {exc}")

st.divider()
st.subheader("AI advisor")
st.caption(
    "Optional. Works with any OpenAI-compatible endpoint (OpenAI, Azure, vLLM, Ollama, "
    "LiteLLM…). Configure via `DEA_AI_MODEL`, `DEA_AI_BASE_URL`, `DEA_AI_API_KEY` or below."
)
env = AdvisorSettings.from_env()
a1, a2, a3 = st.columns(3)
model = a1.text_input("Model", value=env.model)
base_url = a2.text_input(
    "Base URL", value=env.base_url or "", placeholder="https://api.openai.com/v1"
)
api_key = a3.text_input(
    "API key", value="", type="password", placeholder="from environment" if env.api_key else ""
)
settings = AdvisorSettings(model=model, base_url=base_url or None, api_key=api_key or env.api_key)

with st.expander("What will be sent (the complete payload)"):
    payload = Advisor.payload(profile)
    st.caption(
        f"{len(json.dumps(payload)):,} bytes · aggregate statistics only · no encounter ids, "
        "timestamps or individual values"
    )
    st.json(payload, expanded=False)

try:
    import openai  # noqa: F401

    sdk_ok = True
except ImportError:
    sdk_ok = False

if not sdk_ok:
    st.info("Install `dea[ai]` to enable the advisor.")
elif not settings.configured:
    st.info("Set an API key or a base URL to enable the advisor.")
else:
    if st.button("Get recommendations", type="primary"):
        try:
            with st.spinner(f"Asking {settings.model}…"):
                st.session_state["recommendation"] = Advisor(settings).recommend(profile)
        except AdvisorError as exc:
            st.error(str(exc))

rec = st.session_state.get("recommendation")
if rec is not None:
    st.markdown(rec.summary)
    r1, r2, r3 = st.columns(3)
    r1.metric("Recommended grid", rec.resample_every)
    r2.metric("Aggregate", rec.aggregate)
    r3.metric("Default fill", rec.default_fill)
    if rec.variables:
        st.dataframe([v.model_dump() for v in rec.variables], width="stretch", hide_index=True)
    if rec.suggested_filters:
        st.markdown("**Suggested filters**")
        for rule in rec.suggested_filters:
            st.code(f"{rule.label}: {rule.where}  -- {rule.description}", language="sql")
    for note in rec.storage_notes:
        st.caption("Storage: " + note)
    for w in rec.warnings:
        st.warning(w)
    renames = rec.variable_map(full.variables)
    do_rename = False
    if renames:
        do_rename = st.checkbox(
            f"Also rename {len(renames)} variable(s) to canonical names (rewrites the cohort)",
            value=False,
            help=", ".join(f"{k} → {v}" for k, v in list(renames.items())[:12]),
        )
    if st.button("Apply to cohort.yaml"):
        new_cfg = rec.apply_to(cfg, full.variables)
        if do_rename:
            full.config = new_cfg
            with st.spinner("Renaming and rewriting…"):
                full.rename_variables(renames).save()
        else:
            new_cfg.save(full.root / "cohort.yaml")
        state.invalidate()
        st.success("Applied. Charts, exports and filters now use the recommended settings.")
        st.rerun()

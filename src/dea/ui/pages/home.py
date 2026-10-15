"""Cohorts page: pick an existing cohort, import new data, or generate a demo."""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

from dea.io import delete_cohort, detect, import_path, read_table
from dea.ui import state

state.sidebar()
st.title("Diagnostic Expert Advisor")
st.caption("Bring your cohort. Get structure, profiles and views in minutes.")

col_pick, col_new = st.columns([3, 2], gap="large")

with col_pick:
    st.subheader("Your cohorts")
    new_dir = st.text_input("Data directory", value=str(state.data_dir()))
    if Path(new_dir).resolve() != state.data_dir():
        state.set_data_dir(new_dir)
        st.rerun()
    infos = state.available_cohorts()
    if not infos:
        st.info("No cohorts here yet. Import data or generate a demo cohort on the right.")
    for info in infos:
        with st.container(border=True):
            a, b, c = st.columns([3, 2, 1])
            a.markdown(f"**{info.name}**  \n{info.description or ' '}")
            b.metric("Encounters", info.n_encounters)
            is_current = state.cohort_path() == info.path
            if c.button(
                "Open" if not is_current else "Opened",
                key=f"open-{info.path}",
                disabled=is_current,
                type="primary",
            ):
                state.set_cohort(info.path)
                st.switch_page("pages/overview.py")
            with c.popover("Delete"):
                st.caption(f"Remove **{info.name}** and all its files from disk?")
                if st.button("Delete permanently", key=f"del-{info.path}", type="primary"):
                    delete_cohort(info.path)
                    if is_current:
                        st.session_state.pop("cohort_path", None)
                    state.invalidate()
                    st.rerun()

with col_new:
    st.subheader("Add data")
    tab_import, tab_demo = st.tabs(["Import", "Demo cohort"])

    with tab_demo:
        st.write("A synthetic ICU cohort with realistic sampling rates. No real patients.")
        n = st.slider("Encounters", 10, 200, 40, step=10)
        name = st.text_input("Name", value="demo", key="demo-name")
        if st.button("Generate", type="primary"):
            from dea.synth import generate_cohort

            with st.spinner("Generating…"):
                cohort = generate_cohort(n_encounters=n, seed=0, name=name)
                path = cohort.save(state.data_dir() / name)
            state.set_cohort(path)
            st.success(f"Created {name} with {len(cohort)} encounters.")
            st.switch_page("pages/overview.py")

    with tab_import:
        st.write(
            "Upload a CSV/TSV/Parquet table (long or wide), a zip of the DEA v1 CSV layout, "
            "or point to a path on this machine."
        )
        upload = st.file_uploader("File", type=["csv", "tsv", "parquet", "zip", "gz"])
        local = st.text_input("…or a local path", placeholder="/data/my_cohort.csv or a folder")
        source: Path | None = None
        if upload is not None:
            tmp_root = Path(tempfile.mkdtemp(prefix="dea-upload-"))
            target = tmp_root / upload.name
            target.write_bytes(upload.getbuffer())
            if upload.name.endswith(".zip"):
                with zipfile.ZipFile(target) as zf:
                    zf.extractall(tmp_root / "unzipped")
                inner = [p for p in (tmp_root / "unzipped").iterdir()]
                source = (
                    inner[0] if len(inner) == 1 and inner[0].is_dir() else tmp_root / "unzipped"
                )
            else:
                source = target
        elif local.strip():
            source = Path(local.strip()).expanduser()
            if not source.exists():
                st.error(f"{source} does not exist")
                source = None

        if source is not None:
            try:
                det = detect(source)
            except Exception as exc:
                st.error(f"Could not inspect source: {exc}")
                st.stop()
            st.markdown(
                f"Detected layout: **{det.format}**"
                + (f" / {det.table_kind}" if det.table_kind else "")
            )
            for note in det.notes:
                st.caption(note)
            overrides: dict = {}
            static_cols: list[str] = []
            if det.format == "table":
                cols = det.columns
                preview = read_table(source, n_rows=5)
                st.dataframe(preview.to_pandas(), width="stretch", hide_index=True)
                c1, c2 = st.columns(2)
                overrides["encounter_col"] = c1.selectbox(
                    "Encounter id column",
                    cols,
                    index=cols.index(det.encounter_col) if det.encounter_col in cols else 0,
                )
                overrides["time_col"] = c2.selectbox(
                    "Time column",
                    cols,
                    index=cols.index(det.time_col) if det.time_col in cols else 0,
                )
                units = ["auto", "datetime", "s", "ms", "m", "h", "d"]
                overrides["time_unit"] = c1.selectbox(
                    "Time unit",
                    units,
                    index=units.index(det.time_unit),
                    help="datetime = absolute timestamps; otherwise the unit of a numeric offset",
                )
                overrides["table_kind"] = c2.radio(
                    "Shape",
                    ["long", "wide"],
                    index=0 if det.table_kind == "long" else 1,
                    horizontal=True,
                    help="long: one row per observation; wide: one column per variable",
                )
                if overrides["table_kind"] == "long":
                    overrides["variable_col"] = c1.selectbox(
                        "Variable column",
                        cols,
                        index=cols.index(det.variable_col) if det.variable_col in cols else 0,
                    )
                    overrides["value_col"] = c2.selectbox(
                        "Value column",
                        cols,
                        index=cols.index(det.value_col) if det.value_col in cols else 0,
                    )
                else:
                    static_cols = st.multiselect(
                        "Columns that are static per encounter (age, sex, …)",
                        [
                            c
                            for c in cols
                            if c not in (overrides["encounter_col"], overrides["time_col"])
                        ],
                    )
            cohort_name = st.text_input(
                "Cohort name", value=source.stem if source.is_file() else source.name
            )
            if st.button("Import", type="primary", disabled=det.format == "unknown"):
                with st.spinner("Importing…"):
                    try:
                        cohort, report = import_path(
                            source, name=cohort_name, static_cols=static_cols or None, **overrides
                        )
                        dest = state.data_dir() / cohort_name
                        if dest.exists():
                            shutil.rmtree(dest)
                        cohort.save(dest)
                    except Exception as exc:
                        st.error(f"Import failed: {exc}")
                        st.stop()
                state.set_cohort(dest)
                st.success(
                    f"Imported {report.n_encounters} encounters, "
                    f"{report.n_variables} variables, {report.n_observations:,} observations."
                )
                for w in report.warnings:
                    st.warning(w)
                if report.event_columns:
                    st.caption("String columns kept as events: " + ", ".join(report.event_columns))
                if report.dropped_columns:
                    st.caption("Dropped columns: " + ", ".join(report.dropped_columns))
                st.page_link(
                    "pages/overview.py", label="Go to overview", icon=":material/arrow_forward:"
                )

st.divider()
with st.expander("What does DEA do with my data?"):
    st.markdown(
        """
- Data is converted into one structure: a **static** table (one row per encounter) and
  **long-format observations** (encounter, time since admission, variable, value), stored as
  Parquet in the data directory above. Nothing leaves this machine.
- The **Profile** page computes sampling rates, coverage and distributions without any model.
- The optional **AI advisor** only ever receives that profile (aggregate statistics), never rows.
- Researchers get the same cohort as a Polars or pandas frame with `dea.load_cohort(...)`.
        """
    )

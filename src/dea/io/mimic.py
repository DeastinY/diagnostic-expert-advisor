"""Import MIMIC-IV (and its demo) straight from the PhysioNet directory layout.

Expected files below the root (``.csv`` or ``.csv.gz``)::

    icu/icustays   icu/chartevents   icu/d_items
    hosp/patients  hosp/admissions   hosp/labevents   hosp/d_labitems   (labs optional)
    note/discharge note/radiology                                       (MIMIC-IV-Note, optional)

Each ICU stay becomes an encounter. ``time`` is the offset from ``intime``; ``intime`` is kept
as ``admission_time``. Chart and lab items are named by their dictionary label; numeric rows
become observations and text rows become events. Lab events are keyed by hospital admission
in MIMIC, so they are attached to the ICU stay whose window contains them.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from dea.core.config import CohortConfig
from dea.core.model import (
    ENCOUNTER_ID,
    EVENT_SCHEMA,
    LONG_SCHEMA,
    TIME,
    VALUE,
    VARIABLE,
    Cohort,
    collect,
)
from dea.io.importers import ADMISSION_TIME, ImportReport

LAB_LEAD_HOURS = 6  # labs drawn shortly before ICU admission still belong to the stay


def _find(root: Path, rel: str) -> Path | None:
    for suffix in (".csv", ".csv.gz"):
        p = root / f"{rel}{suffix}"
        if p.exists():
            return p
    return None


def _scan(root: Path, rel: str, required: bool = True) -> pl.LazyFrame | None:
    path = _find(root, rel)
    if path is None:
        if required:
            raise FileNotFoundError(f"{rel}.csv[.gz] not found under {root}")
        return None
    kwargs = dict(try_parse_dates=True, infer_schema_length=10_000, null_values=["", "NULL"])
    if path.suffix == ".gz":  # compressed CSV cannot be scanned lazily
        return pl.read_csv(path, **kwargs).lazy()
    return pl.scan_csv(path, **kwargs)


def is_mimic_dir(path: Path | str) -> bool:
    root = Path(path)
    return _find(root, "icu/icustays") is not None and _find(root, "icu/chartevents") is not None


def _split(frame: pl.LazyFrame) -> tuple[pl.LazyFrame, pl.LazyFrame]:
    """Rows with a numeric value -> observations, rows with only text -> events."""
    base = frame.filter(pl.col(TIME).is_not_null())
    dynamic = base.filter(pl.col("valuenum").is_not_null()).select(
        ENCOUNTER_ID, TIME, VARIABLE, pl.col("valuenum").cast(pl.Float64()).alias(VALUE)
    )
    events = base.filter(pl.col("valuenum").is_null() & pl.col("value").is_not_null()).select(
        ENCOUNTER_ID, TIME, VARIABLE, pl.col("value").cast(pl.String()).alias(VALUE)
    )
    return dynamic.select(list(LONG_SCHEMA)), events.select(list(EVENT_SCHEMA))


def import_mimic(path: Path | str, name: str | None = None) -> tuple[Cohort, ImportReport]:
    root = Path(path)
    report = ImportReport(source=str(root), format="mimic-iv")
    stays = _scan(root, "icu/icustays").select(
        pl.col("stay_id").cast(pl.String()).alias(ENCOUNTER_ID),
        "subject_id",
        "hadm_id",
        pl.col("intime").cast(pl.Datetime("us")).alias(ADMISSION_TIME),
        pl.col("outtime").cast(pl.Datetime("us")),
        pl.col("los").alias("icu_los_days"),
        "first_careunit",
    )
    patients = _scan(root, "hosp/patients", required=False)
    if patients is not None:
        stays = stays.join(
            patients.select(
                "subject_id", pl.col("gender").alias("sex"), pl.col("anchor_age").alias("age")
            ),
            on="subject_id",
            how="left",
        )
    admissions = _scan(root, "hosp/admissions", required=False)
    if admissions is not None:
        stays = stays.join(
            admissions.select(
                "hadm_id",
                "admission_type",
                "insurance",
                "race",
                pl.when(pl.col("hospital_expire_flag") == 1)
                .then(pl.lit("deceased"))
                .otherwise(pl.lit("survived"))
                .alias("outcome"),
            ),
            on="hadm_id",
            how="left",
        )
    static = collect(stays)
    window = static.lazy().select(ENCOUNTER_ID, "hadm_id", ADMISSION_TIME, "outtime")

    d_items = _scan(root, "icu/d_items").select("itemid", pl.col("label").alias(VARIABLE))
    chart = (
        _scan(root, "icu/chartevents")
        .select(
            pl.col("stay_id").cast(pl.String()).alias(ENCOUNTER_ID),
            "charttime",
            "itemid",
            "value",
            "valuenum",
            "valueuom",
        )
        .join(d_items, on="itemid", how="inner")
        .join(window.select(ENCOUNTER_ID, ADMISSION_TIME), on=ENCOUNTER_ID, how="inner")
        .with_columns(
            (pl.col("charttime") - pl.col(ADMISSION_TIME)).cast(LONG_SCHEMA[TIME]).alias(TIME)
        )
    )
    dynamics, events_list, unit_sources = [], [], [chart.select(VARIABLE, "valueuom")]
    dyn, evt = _split(chart)
    dynamics.append(dyn)
    events_list.append(evt)

    labevents = _scan(root, "hosp/labevents", required=False)
    if labevents is not None:
        d_lab = _scan(root, "hosp/d_labitems").select("itemid", pl.col("label").alias(VARIABLE))
        labs = (
            labevents.select("hadm_id", "charttime", "itemid", "value", "valuenum", "valueuom")
            .join(d_lab, on="itemid", how="inner")
            .join(window, on="hadm_id", how="inner")
            .filter(
                (pl.col("charttime") >= pl.col(ADMISSION_TIME) - pl.duration(hours=LAB_LEAD_HOURS))
                & (pl.col("charttime") <= pl.col("outtime"))
            )
            .with_columns(
                (pl.col("charttime") - pl.col(ADMISSION_TIME)).cast(LONG_SCHEMA[TIME]).alias(TIME)
            )
        )
        dyn, evt = _split(labs)
        dynamics.append(dyn)
        events_list.append(evt)
        unit_sources.append(labs.select(VARIABLE, "valueuom"))
    else:
        report.warnings.append("hosp/labevents not found; lab values skipped")

    for rel, label in (
        ("note/discharge", "Discharge note"),
        ("note/radiology", "Radiology report"),
    ):
        notes = _scan(root, rel, required=False)
        if notes is None:
            continue
        note_events = (
            notes.select("hadm_id", "charttime", "text")
            .join(window, on="hadm_id", how="inner")
            .with_columns(
                (pl.col("charttime") - pl.col(ADMISSION_TIME)).cast(LONG_SCHEMA[TIME]).alias(TIME)
            )
            .filter(pl.col("text").is_not_null())
            .select(
                ENCOUNTER_ID,
                TIME,
                pl.lit(label).alias(VARIABLE),
                pl.col("text").cast(pl.String()).alias(VALUE),
            )
        )
        events_list.append(note_events.select(list(EVENT_SCHEMA)))

    units = collect(
        pl.concat(unit_sources)
        .drop_nulls()
        .group_by(VARIABLE, "valueuom")
        .agg(pl.len().alias("n"))
        .sort(["n"], descending=True)
        .unique(subset=[VARIABLE], keep="first")
    )
    config = CohortConfig(
        name=name or root.name,
        description="Imported from MIMIC-IV layout; one encounter per ICU stay.",
        source=str(root),
        variable_units={v: u for v, u in units.select(VARIABLE, "valueuom").iter_rows() if u},
        filters=[],
    )
    cohort = Cohort(
        static.drop("outtime").sort(ENCOUNTER_ID),
        pl.concat(dynamics),
        events=pl.concat(events_list),
        config=config,
    )
    report.n_encounters = len(cohort)
    counts = cohort.variable_counts()
    report.n_variables = counts.height
    report.n_observations = int(counts["n_observations"].sum()) if counts.height else 0
    report.n_events = int(collect(cohort.events.select(pl.len())).item())
    report.mapping = {
        "encounter": "stay_id",
        "time": "charttime - intime",
        "variables": "d_items / d_labitems labels",
    }
    return cohort, report

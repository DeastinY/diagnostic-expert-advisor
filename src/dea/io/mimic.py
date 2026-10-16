"""Import MIMIC-IV (and its demo) straight from the PhysioNet directory layout.

Expected files below the root (``.csv`` or ``.csv.gz``)::

    icu/icustays   icu/chartevents   icu/d_items
    icu/inputevents icu/procedureevents icu/outputevents                  (optional)
    hosp/patients  hosp/admissions   hosp/labevents   hosp/d_labitems   (labs optional)
    note/discharge note/radiology                                       (MIMIC-IV-Note, optional)

Each ICU stay becomes an encounter. ``time`` is the offset from ``intime``; ``intime`` is kept
as ``admission_time``. Chart and lab items are named by their dictionary label; numeric rows
become observations and text rows become events. Lab events are keyed by hospital admission
in MIMIC, so they are attached to the ICU stay whose window contains them. Units are read per
row (``valueuom``) and variables the dictionary knows are converted to one canonical unit,
so ``Temperature Fahrenheit`` arrives in °C and ``Inspired O2 Fraction`` as a fraction.
ICD-9 and ICD-10 diagnoses become ``dx_<group>`` flags and an ``icd_codes`` static column.
Infusions (``inputevents``, value = rate) and procedures (``procedureevents``) become
intervals; ``outputevents`` (urine output, drains) become numeric observations.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from dea.core.config import CohortConfig
from dea.core.model import (
    ENCOUNTER_ID,
    END,
    EVENT_SCHEMA,
    INTERVAL_SCHEMA,
    LONG_SCHEMA,
    START,
    TIME,
    VALUE,
    VARIABLE,
    Cohort,
    collect,
)
from dea.io.importers import ADMISSION_TIME, ImportReport, harmonize_units
from dea.standards.icd import diagnosis_table

LAB_LEAD_HOURS = 6  # labs drawn shortly before ICU admission still belong to the stay
SMALL_GZ_BYTES = 64 * 1024 * 1024  # larger gzipped tables are converted to Parquet parts


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
    if path.suffix == ".gz":
        if path.stat().st_size < SMALL_GZ_BYTES:  # demo-sized: read directly
            return pl.read_csv(path, **kwargs).lazy()
        from dea.io.compressed import scan_compressed_csv

        return scan_compressed_csv(path, **kwargs)
    return pl.scan_csv(path, **kwargs)


def _datetime(lf: pl.LazyFrame, *columns: str) -> pl.LazyFrame:
    """Make sure time stamp columns are datetimes, whatever the CSV reader inferred."""
    schema = lf.collect_schema()
    exprs = []
    for c in columns:
        if schema[c] == pl.String:
            exprs.append(pl.col(c).str.to_datetime(strict=False).alias(c))
        elif not schema[c].is_temporal():
            exprs.append(pl.col(c).cast(pl.Datetime("us")).alias(c))
    return lf.with_columns(exprs) if exprs else lf


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
    diagnoses = _scan(root, "hosp/diagnoses_icd", required=False)
    if diagnoses is not None:
        # ICD-9 codes such as 4019 look numeric to a CSV reader: force text on both sides
        code = pl.col("icd_code").cast(pl.String()).str.strip_chars()
        version = pl.col("icd_version").cast(pl.Int64())
        dx = diagnoses.select("hadm_id", "seq_num", code, version).join(
            window.select(ENCOUNTER_ID, "hadm_id"), on="hadm_id", how="inner"
        )
        titles = _scan(root, "hosp/d_icd_diagnoses", required=False)
        if titles is not None:
            dx = dx.join(
                titles.select(code, version, pl.col("long_title").alias("title")),
                on=["icd_code", "icd_version"],
                how="left",
            )
        table = diagnosis_table(collect(dx), title_col="title" if titles is not None else None)
        static = static.join(table, on=ENCOUNTER_ID, how="left")
    else:
        report.warnings.append("hosp/diagnoses_icd not found; no diagnosis columns")

    d_items = _scan(root, "icu/d_items").select(
        pl.col("itemid").cast(pl.Int64()), pl.col("label").alias(VARIABLE)
    )
    chart = (
        _datetime(_scan(root, "icu/chartevents"), "charttime")
        .select(
            pl.col("stay_id").cast(pl.String()).alias(ENCOUNTER_ID),
            "charttime",
            pl.col("itemid").cast(pl.Int64()),
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
    keep = [
        ENCOUNTER_ID,
        TIME,
        VARIABLE,
        pl.col("value").cast(pl.String()),
        pl.col("valuenum").cast(pl.Float64()),
        pl.col("valueuom").cast(pl.String()),
    ]
    dynamics, events_list, sources = [], [], [chart.select(keep)]

    labevents = _scan(root, "hosp/labevents", required=False)
    if labevents is not None:
        d_lab = _scan(root, "hosp/d_labitems").select(
            pl.col("itemid").cast(pl.Int64()), pl.col("label").alias(VARIABLE)
        )
        labs = (
            labevents.select(
                "hadm_id",
                "charttime",
                pl.col("itemid").cast(pl.Int64()),
                "value",
                "valuenum",
                "valueuom",
            )
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
        sources.append(labs.select(keep))
    else:
        report.warnings.append("hosp/labevents not found; lab values skipped")
    outputs = _scan(root, "icu/outputevents", required=False)
    if outputs is not None:  # urine output and drains: numeric observations
        out_rows = (
            _datetime(outputs, "charttime")
            .select(
                pl.col("stay_id").cast(pl.String()).alias(ENCOUNTER_ID),
                "charttime",
                pl.col("itemid").cast(pl.Int64()),
                pl.col("value").cast(pl.Float64()).alias("valuenum"),
                pl.col("value").cast(pl.String()),
                "valueuom",
            )
            .join(d_items, on="itemid", how="inner")
            .join(window.select(ENCOUNTER_ID, ADMISSION_TIME), on=ENCOUNTER_ID, how="inner")
            .with_columns(
                (pl.col("charttime") - pl.col(ADMISSION_TIME)).cast(LONG_SCHEMA[TIME]).alias(TIME)
            )
        )
        sources.append(out_rows.select(keep))
    numeric = pl.concat([f.filter(pl.col("valuenum").is_not_null()) for f in sources])
    numeric, variable_units, codes = harmonize_units(numeric, "valueuom", report, "valuenum")
    for frame in (numeric, *(f.filter(pl.col("valuenum").is_null()) for f in sources)):
        dyn, evt = _split(frame)
        dynamics.append(dyn)
        events_list.append(evt)

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

    # interventions with a duration: infusions (rate) and procedures such as ventilation
    intervals = []
    span = window.select(ENCOUNTER_ID, ADMISSION_TIME)
    inputs = _scan(root, "icu/inputevents", required=False)
    if inputs is not None:
        iv = (
            _datetime(inputs, "starttime", "endtime")
            .select(
                pl.col("stay_id").cast(pl.String()).alias(ENCOUNTER_ID),
                "starttime",
                "endtime",
                pl.col("itemid").cast(pl.Int64()),
                pl.coalesce(pl.col("rate"), pl.col("amount")).cast(pl.Float64()).alias(VALUE),
                pl.coalesce(pl.col("rateuom"), pl.col("amountuom")).cast(pl.String()).alias("uom"),
            )
            .join(d_items, on="itemid", how="inner")
            .join(span, on=ENCOUNTER_ID, how="inner")
        )
        intervals.append(iv)
    procedures = _scan(root, "icu/procedureevents", required=False)
    if procedures is not None:
        pv = (
            _datetime(procedures, "starttime", "endtime")
            .select(
                pl.col("stay_id").cast(pl.String()).alias(ENCOUNTER_ID),
                "starttime",
                "endtime",
                pl.col("itemid").cast(pl.Int64()),
                pl.lit(1.0).alias(VALUE),
                pl.lit(None, dtype=pl.String()).alias("uom"),
            )
            .join(d_items, on="itemid", how="inner")
            .join(span, on=ENCOUNTER_ID, how="inner")
        )
        intervals.append(pv)
    interval_frame = None
    if intervals:
        interval_frame = pl.concat(intervals).with_columns(
            (pl.col("starttime") - pl.col(ADMISSION_TIME))
            .cast(INTERVAL_SCHEMA[START])
            .alias(START),
            (pl.col("endtime") - pl.col(ADMISSION_TIME)).cast(INTERVAL_SCHEMA[END]).alias(END),
        )
        unit_rows = collect(
            interval_frame.drop_nulls("uom")
            .group_by(VARIABLE, "uom")
            .agg(pl.len().alias("n"))
            .sort("n", descending=True)
            .unique(subset=[VARIABLE], keep="first")
        )
        for v, u in unit_rows.select(VARIABLE, "uom").iter_rows():
            variable_units.setdefault(v, u)
        interval_frame = interval_frame.filter(
            pl.col(START).is_not_null() & pl.col(END).is_not_null()
        ).select(list(INTERVAL_SCHEMA))

    config = CohortConfig(
        name=name or root.name,
        description="Imported from MIMIC-IV layout; one encounter per ICU stay.",
        source=str(root),
        variable_units=variable_units,
        variable_codes=codes,
        filters=[],
    )
    cohort = Cohort(
        static.drop("outtime").sort(ENCOUNTER_ID),
        pl.concat(dynamics),
        events=pl.concat(events_list),
        intervals=interval_frame,
        config=config,
    )
    report.n_encounters = len(cohort)
    counts = cohort.variable_counts()
    report.n_variables = counts.height
    report.n_observations = int(counts["n_observations"].sum()) if counts.height else 0
    report.n_events = int(collect(cohort.events.select(pl.len())).item())
    report.n_intervals = int(collect(cohort.intervals.select(pl.len())).item())
    report.mapping = {
        "encounter": "stay_id",
        "time": "charttime - intime",
        "variables": "d_items / d_labitems labels",
    }
    return cohort, report

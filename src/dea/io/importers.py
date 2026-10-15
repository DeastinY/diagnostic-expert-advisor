"""Turn whatever a researcher has into a :class:`~dea.core.Cohort`.

Three input shapes are understood:

* **table (long)** - one row per observation: encounter, time, variable, value.
* **table (wide)** - one row per encounter and time, one column per variable.
* **csv tree** - the DEA v1 layout: ``static.csv`` plus ``<id>/dynamic.csv`` per encounter.

:func:`detect` guesses the shape and the key columns; :func:`import_path` applies the guess
(or your overrides) and returns the cohort together with an :class:`ImportReport` that lists
everything that was dropped or assumed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import polars as pl

from dea.core.config import CohortConfig
from dea.core.model import ENCOUNTER_ID, LONG_SCHEMA, TIME, VALUE, VARIABLE, Cohort

TimeUnit = Literal["auto", "datetime", "s", "ms", "m", "h", "d"]
_UNIT_SECONDS = {"s": 1.0, "ms": 0.001, "m": 60.0, "h": 3600.0, "d": 86400.0}

_ENCOUNTER_HINT = re.compile(
    r"(encounter|stay|hadm|admission|case|patient|subject|visit|episode|^id$|_id$|^pid$)", re.I
)
_TIME_HINT = re.compile(
    r"(^time|charttime|timestamp|datetime|offset|minute|hour|^date|_time$|_at$)", re.I
)
_VARIABLE_HINT = re.compile(
    r"(variable|item|label|parameter|feature|concept|measurement|signal|^name$)", re.I
)
_VALUE_HINT = re.compile(r"(^value|valuenum|result|measurement_value|^val$)", re.I)
_UNIT_BY_NAME = (
    (re.compile(r"(min|minute)", re.I), "m"),
    (re.compile(r"(hour|hrs|^h$)", re.I), "h"),
    (re.compile(r"(sec|second)", re.I), "s"),
    (re.compile(r"(day)", re.I), "d"),
    (re.compile(r"(ms|milli)", re.I), "ms"),
)


@dataclass
class ImportReport:
    source: str
    format: str
    n_encounters: int = 0
    n_variables: int = 0
    n_observations: int = 0
    mapping: dict[str, Any] = field(default_factory=dict)
    dropped_columns: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        lines = [
            f"source: {self.source}",
            f"format: {self.format}",
            f"encounters: {self.n_encounters}",
            f"variables: {self.n_variables}",
            f"observations: {self.n_observations}",
        ]
        if self.mapping:
            lines.append("mapping: " + ", ".join(f"{k}={v}" for k, v in self.mapping.items() if v))
        if self.dropped_columns:
            lines.append("dropped non-numeric columns: " + ", ".join(self.dropped_columns))
        lines.extend(f"warning: {w}" for w in self.warnings)
        return lines


@dataclass
class Detection:
    """Best guess at how a source is laid out. Every field can be overridden on import."""

    source: Path
    format: Literal["dea", "csv_tree", "table", "unknown"]
    table_kind: Literal["long", "wide"] | None = None
    encounter_col: str | None = None
    time_col: str | None = None
    variable_col: str | None = None
    value_col: str | None = None
    time_unit: TimeUnit = "auto"
    columns: list[str] = field(default_factory=list)
    dtypes: dict[str, str] = field(default_factory=dict)
    n_rows: int | None = None
    notes: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------- reading


def read_table(path: Path | str, n_rows: int | None = None) -> pl.DataFrame:
    """Read CSV/TSV/Parquet/JSON-lines into a DataFrame."""
    p = Path(path)
    suffixes = "".join(p.suffixes).lower()
    if suffixes.endswith((".parquet", ".pq")):
        df = pl.read_parquet(p)
        return df.head(n_rows) if n_rows else df
    if suffixes.endswith((".jsonl", ".ndjson")):
        df = pl.read_ndjson(p)
        return df.head(n_rows) if n_rows else df
    sep = "\t" if ".tsv" in suffixes else ","
    return pl.read_csv(
        p,
        separator=sep,
        n_rows=n_rows,
        infer_schema_length=10_000,
        try_parse_dates=True,
        null_values=["", "NA", "NaN", "nan", "NULL", "null", "None"],
    )


# --------------------------------------------------------------------------- detection


def _guess_time_unit(df: pl.DataFrame, time_col: str, encounter_col: str | None) -> TimeUnit:
    dtype = df.schema[time_col]
    if dtype.is_temporal():
        return "datetime"
    for pattern, unit in _UNIT_BY_NAME:
        if pattern.search(time_col):
            return unit  # type: ignore[return-value]
    if not dtype.is_numeric():
        return "auto"
    if encounter_col and encounter_col in df.columns:
        spans = df.group_by(encounter_col).agg(pl.col(time_col).max() - pl.col(time_col).min())
        typical = spans.get_column(time_col).median()
    else:
        typical = df.get_column(time_col).max()
    if typical is None:
        return "m"
    typical = float(typical)
    if typical < 100:
        return "h"
    if typical < 1e5:
        return "m"
    if typical < 1e8:
        return "s"
    return "ms"


def detect_table(df: pl.DataFrame, source: Path | str = "<frame>") -> Detection:
    det = Detection(
        source=Path(source),
        format="table",
        columns=df.columns,
        dtypes={c: str(t) for c, t in df.schema.items()},
        n_rows=df.height,
    )
    cols = df.columns
    # encounter column
    hinted = [c for c in cols if _ENCOUNTER_HINT.search(c) and not _TIME_HINT.search(c)]
    if hinted:
        det.encounter_col = hinted[0]
    else:
        candidates = []
        for c in cols:
            if df.schema[c].is_temporal() or df.schema[c].is_float():
                continue
            nu = df.get_column(c).n_unique()
            if 1 <= nu <= max(1, df.height // 2):
                candidates.append((nu, c))
        if candidates:
            det.encounter_col = sorted(candidates)[0][1]
            det.notes.append(
                f"no id-like column name; guessed '{det.encounter_col}' by cardinality"
            )
    # time column
    temporal = [c for c in cols if df.schema[c].is_temporal()]
    hinted_t = [c for c in cols if _TIME_HINT.search(c) and c != det.encounter_col]
    det.time_col = (hinted_t or temporal or [None])[0]
    if det.time_col is None:
        det.notes.append("no time column found")
    # long vs wide
    var_hint = [
        c for c in cols if _VARIABLE_HINT.search(c) and c not in (det.encounter_col, det.time_col)
    ]
    val_hint = [
        c for c in cols if _VALUE_HINT.search(c) and c not in (det.encounter_col, det.time_col)
    ]
    rest = [c for c in cols if c not in (det.encounter_col, det.time_col)]
    if var_hint and val_hint:
        det.table_kind, det.variable_col, det.value_col = "long", var_hint[0], val_hint[0]
    elif len(rest) == 2 and df.schema[rest[0]] == pl.String and df.schema[rest[1]].is_numeric():
        det.table_kind, det.variable_col, det.value_col = "long", rest[0], rest[1]
    elif len(rest) == 2 and df.schema[rest[1]] == pl.String and df.schema[rest[0]].is_numeric():
        det.table_kind, det.variable_col, det.value_col = "long", rest[1], rest[0]
    else:
        det.table_kind = "wide"
    if det.time_col:
        det.time_unit = _guess_time_unit(df, det.time_col, det.encounter_col)
    return det


def detect(path: Path | str) -> Detection:
    """Inspect a path and guess how to import it."""
    p = Path(path)
    from dea.io.storage import is_cohort_dir  # local import to avoid a cycle at module load

    if p.is_dir():
        if is_cohort_dir(p):
            return Detection(source=p, format="dea")
        if (p / "static.csv").exists() or any(
            (d / "dynamic.csv").exists() for d in p.iterdir() if d.is_dir()
        ):
            return Detection(source=p, format="csv_tree", time_unit="m")
        tables = sorted(x for x in p.iterdir() if x.suffix.lower() in (".csv", ".tsv", ".parquet"))
        if len(tables) == 1:
            return detect(tables[0])
        return Detection(
            source=p, format="unknown", notes=["directory holds no recognizable layout"]
        )
    if not p.exists():
        raise FileNotFoundError(p)
    try:
        sample = read_table(p, n_rows=50_000)
    except Exception as exc:
        return Detection(source=p, format="unknown", notes=[f"could not read table: {exc}"])
    det = detect_table(sample, p)
    det.n_rows = None  # sample only
    return det


# -------------------------------------------------------------------------- conversion


def _to_duration(
    df: pl.DataFrame, time_col: str, encounter_col: str, unit: TimeUnit, report: ImportReport
) -> pl.DataFrame:
    dtype = df.schema[time_col]
    if unit == "auto":
        unit = "datetime" if dtype.is_temporal() else _guess_time_unit(df, time_col, encounter_col)
        report.warnings.append(f"time unit guessed as '{unit}' for column '{time_col}'")
    if dtype == pl.Duration:
        return df.with_columns(pl.col(time_col).cast(LONG_SCHEMA[TIME]).alias(TIME))
    if unit == "datetime":
        if dtype == pl.String:
            df = df.with_columns(pl.col(time_col).str.to_datetime(strict=False))
            dtype = df.schema[time_col]
        if dtype == pl.Date:
            df = df.with_columns(pl.col(time_col).cast(pl.Datetime("us")))
        if not df.schema[time_col].is_temporal():
            raise ValueError(f"column '{time_col}' cannot be parsed as datetime")
        return df.with_columns(
            (pl.col(time_col) - pl.col(time_col).min().over(encounter_col))
            .cast(LONG_SCHEMA[TIME])
            .alias(TIME)
        )
    if dtype == pl.String:
        df = df.with_columns(pl.col(time_col).cast(pl.Float64(), strict=False))
    if not df.schema[time_col].is_numeric():
        raise ValueError(f"column '{time_col}' is neither numeric nor datetime")
    factor = _UNIT_SECONDS[unit]
    return df.with_columns(
        pl.duration(
            seconds=(pl.col(time_col).cast(pl.Float64()) * factor).round(0).cast(pl.Int64())
        )
        .cast(LONG_SCHEMA[TIME])
        .alias(TIME)
    )


def wide_to_long(
    df: pl.DataFrame,
    encounter_col: str,
    time_col: str,
    time_unit: TimeUnit = "auto",
    report: ImportReport | None = None,
    exclude: list[str] | None = None,
) -> pl.DataFrame:
    """Unpivot a wide table into the long schema, dropping non-numeric columns."""
    report = report or ImportReport(source="<frame>", format="table")
    exclude = set(exclude or [])
    df = df.filter(pl.col(encounter_col).is_not_null())
    df = _to_duration(df, time_col, encounter_col, time_unit, report)
    value_cols = []
    for c in df.columns:
        if c in (encounter_col, time_col, TIME) or c in exclude or c.startswith("Unnamed"):
            continue
        dtype = df.schema[c]
        if dtype.is_numeric() or dtype == pl.Boolean:
            value_cols.append(c)
            continue
        if dtype == pl.String:
            casted = df.get_column(c).cast(pl.Float64(), strict=False)
            if casted.null_count() <= df.get_column(c).null_count() + max(1, df.height // 100):
                df = df.with_columns(casted.alias(c))
                value_cols.append(c)
                continue
        report.dropped_columns.append(c)
    if not value_cols:
        raise ValueError("no numeric variable columns found")
    long = (
        df.select(
            pl.col(encounter_col).cast(pl.String()).alias(ENCOUNTER_ID),
            TIME,
            *[pl.col(c).cast(pl.Float64()) for c in value_cols],
        )
        .unpivot(
            index=[ENCOUNTER_ID, TIME], on=value_cols, variable_name=VARIABLE, value_name=VALUE
        )
        .filter(pl.col(VALUE).is_not_null() & pl.col(TIME).is_not_null())
    )
    return long.select(list(LONG_SCHEMA))


def _finish(
    long: pl.DataFrame, static: pl.DataFrame | None, name: str, report: ImportReport, source: str
) -> Cohort:
    ids = long.select(pl.col(ENCOUNTER_ID).unique()).sort(ENCOUNTER_ID)
    if static is None:
        static = ids
    else:
        static = static.with_columns(pl.col(ENCOUNTER_ID).cast(pl.String()))
        missing = ids.join(static, on=ENCOUNTER_ID, how="anti")
        if missing.height:
            report.warnings.append(
                f"{missing.height} encounter(s) have observations but no static row"
            )
            static = pl.concat([static, missing], how="diagonal_relaxed")
        orphan = static.join(ids, on=ENCOUNTER_ID, how="anti").height
        if orphan:
            report.warnings.append(f"{orphan} static row(s) have no observations")
    config = CohortConfig(name=name, source=source)
    cohort = Cohort(static.sort(ENCOUNTER_ID), long, config=config)
    report.n_encounters = len(cohort)
    report.n_variables = len(cohort.variables)
    report.n_observations = long.height
    return cohort


def import_wide(
    df: pl.DataFrame,
    encounter_col: str,
    time_col: str,
    time_unit: TimeUnit = "auto",
    static: pl.DataFrame | None = None,
    static_cols: list[str] | None = None,
    name: str = "cohort",
    source: str = "<frame>",
) -> tuple[Cohort, ImportReport]:
    """Import a wide table (one column per variable).

    ``static_cols`` are columns that are constant within an encounter (age, sex, ...); their
    first value per encounter moves to the static table instead of being treated as a
    time series.
    """
    report = ImportReport(
        source=str(source),
        format="table/wide",
        mapping={"encounter": encounter_col, "time": time_col, "time_unit": time_unit},
    )
    static_cols = static_cols or []
    if static_cols:
        pulled = df.group_by(encounter_col).agg(
            [pl.col(c).drop_nulls().first() for c in static_cols]
        )
        pulled = pulled.rename({encounter_col: ENCOUNTER_ID})
        static = (
            pulled
            if static is None
            else static.join(pulled, on=ENCOUNTER_ID, how="full", coalesce=True)
        )
    long = wide_to_long(df, encounter_col, time_col, time_unit, report, exclude=static_cols)
    return _finish(long, static, name, report, str(source)), report


def import_long(
    df: pl.DataFrame,
    encounter_col: str,
    time_col: str,
    variable_col: str,
    value_col: str,
    time_unit: TimeUnit = "auto",
    static: pl.DataFrame | None = None,
    name: str = "cohort",
    source: str = "<frame>",
) -> tuple[Cohort, ImportReport]:
    """Import a long table (one row per observation)."""
    report = ImportReport(
        source=str(source),
        format="table/long",
        mapping={
            "encounter": encounter_col,
            "time": time_col,
            "variable": variable_col,
            "value": value_col,
            "time_unit": time_unit,
        },
    )
    df = df.filter(pl.col(encounter_col).is_not_null() & pl.col(variable_col).is_not_null())
    df = _to_duration(df, time_col, encounter_col, time_unit, report)
    values = df.get_column(value_col)
    if not values.dtype.is_numeric():
        casted = values.cast(pl.Float64(), strict=False)
        lost = casted.null_count() - values.null_count()
        if lost:
            report.warnings.append(f"{lost} non-numeric value(s) in '{value_col}' were dropped")
        df = df.with_columns(casted.alias(value_col))
    long = df.select(
        pl.col(encounter_col).cast(pl.String()).alias(ENCOUNTER_ID),
        TIME,
        pl.col(variable_col).cast(pl.String()).alias(VARIABLE),
        pl.col(value_col).cast(pl.Float64()).alias(VALUE),
    ).filter(pl.col(VALUE).is_not_null() & pl.col(TIME).is_not_null())
    return _finish(long, static, name, report, str(source)), report


def _read_legacy_static(path: Path, report: ImportReport) -> pl.DataFrame | None:
    file = path / "static.csv"
    if not file.exists():
        report.warnings.append("no static.csv found; static table holds only ids")
        return None
    static = pl.read_csv(file, infer_schema_length=10_000)
    first = static.columns[0]
    id_col = None
    if first == "" or first.startswith("Unnamed") or first.startswith("_duplicated"):
        id_col = first
    else:
        for c in static.columns:
            if _ENCOUNTER_HINT.search(c):
                id_col = c
                break
    if id_col is None:
        report.warnings.append("static.csv has no id column; using row number as encounter id")
        static = static.with_row_index(ENCOUNTER_ID)
    else:
        static = static.rename({id_col: ENCOUNTER_ID})
    static = static.with_columns(pl.col(ENCOUNTER_ID).cast(pl.String()))
    static = static.drop([c for c in static.columns if c.startswith("Unnamed") or c == ""])
    return static.unique(subset=[ENCOUNTER_ID], keep="first")


def import_csv_tree(
    path: Path | str, time_unit: TimeUnit = "m", name: str | None = None
) -> tuple[Cohort, ImportReport]:
    """Import the DEA v1 layout (``static.csv`` plus ``<id>/dynamic.csv``)."""
    root = Path(path)
    report = ImportReport(source=str(root), format="csv_tree", mapping={"time_unit": time_unit})
    static = _read_legacy_static(root, report)
    parts = []
    for enc_dir in sorted(d for d in root.iterdir() if d.is_dir()):
        file = enc_dir / "dynamic.csv"
        if not file.exists():
            continue
        try:
            df = pl.read_csv(file, infer_schema_length=10_000)
        except Exception as exc:
            report.warnings.append(f"could not read {file}: {exc}")
            continue
        time_col = df.columns[0]
        df = df.with_columns(pl.lit(enc_dir.name).alias("__encounter"))
        sub = ImportReport(source=str(file), format="csv_tree")
        try:
            long = wide_to_long(df, "__encounter", time_col, time_unit, sub)
        except ValueError as exc:
            report.warnings.append(f"{enc_dir.name}: {exc}")
            continue
        for c in sub.dropped_columns:
            if c not in report.dropped_columns:
                report.dropped_columns.append(c)
        parts.append(long)
    if not parts:
        raise ValueError(f"no <id>/dynamic.csv files found under {root}")
    long = pl.concat(parts)
    cohort = _finish(long, static, name or root.name, report, str(root))
    cohort.config.processing.resample_every = None
    return cohort, report


def import_path(
    path: Path | str,
    name: str | None = None,
    detection: Detection | None = None,
    static_path: Path | str | None = None,
    **overrides: Any,
) -> tuple[Cohort, ImportReport]:
    """Detect and import ``path``. Keyword overrides replace fields of the detection."""
    from dea.io.storage import load_cohort

    p = Path(path)
    det = detection or detect(p)
    for key, value in overrides.items():
        if value is not None:
            setattr(det, key, value)
    cohort_name = name or p.stem
    if det.format == "dea":
        cohort = load_cohort(p)
        return cohort, ImportReport(
            source=str(p), format="dea", n_encounters=len(cohort), n_variables=len(cohort.variables)
        )
    if det.format == "csv_tree":
        unit = det.time_unit if det.time_unit != "auto" else "m"
        return import_csv_tree(p, time_unit=unit, name=cohort_name)
    if det.format != "table":
        raise ValueError(f"cannot import {p}: {'; '.join(det.notes) or 'unknown layout'}")
    df = read_table(p)
    static = read_table(static_path) if static_path else None
    if static is not None and ENCOUNTER_ID not in static.columns:
        hinted = [c for c in static.columns if _ENCOUNTER_HINT.search(c)]
        if not hinted:
            raise ValueError("static table needs an encounter id column")
        static = static.rename({hinted[0]: ENCOUNTER_ID})
    if not det.encounter_col or not det.time_col:
        raise ValueError("encounter and time columns are required; pass encounter_col=/time_col=")
    if det.table_kind == "long":
        if not det.variable_col or not det.value_col:
            raise ValueError("long tables need variable_col= and value_col=")
        return import_long(
            df,
            det.encounter_col,
            det.time_col,
            det.variable_col,
            det.value_col,
            det.time_unit,
            static,
            cohort_name,
            str(p),
        )
    return import_wide(
        df,
        det.encounter_col,
        det.time_col,
        det.time_unit,
        static,
        overrides.get("static_cols"),
        cohort_name,
        str(p),
    )

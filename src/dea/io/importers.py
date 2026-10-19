"""Turn whatever a researcher has into a :class:`~dea.core.Cohort`.

Three input shapes are understood:

* **table (long)** - one row per observation: encounter, time, variable, value.
* **table (wide)** - one row per encounter and time, one column per variable.
* **csv tree** - the DEA v1 layout: ``static.csv`` plus ``<id>/dynamic.csv`` per encounter.
* **MIMIC-IV** and **FHIR R4** layouts are handled by :mod:`dea.io.mimic` and
  :mod:`dea.standards.fhir`; :func:`detect` recognizes them.

:func:`detect` guesses the shape and the key columns; :func:`import_path` applies the guess
(or your overrides) and returns the cohort together with an :class:`ImportReport`.

Everything is lazy: sources are scanned, not read, and the result streams to Parquet when
the cohort is saved, so tables far larger than memory import fine. Numeric columns become
observations, string columns become events, and absolute timestamps are kept as an
``admission_time`` static column while ``time`` becomes the offset from admission.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

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
from dea.io import progress

TimeUnit = Literal["auto", "datetime", "s", "ms", "m", "h", "d"]
ADMISSION_TIME = "admission_time"
UNIT = "unit"
IMPORT_REPORT_FILE = "import.json"
_UNIT_SECONDS = {"s": 1.0, "ms": 0.001, "m": 60.0, "h": 3600.0, "d": 86400.0}
_SAMPLE_ROWS = 50_000

_ENCOUNTER_HINT = re.compile(
    r"(encounter|stay|hadm|admission|case|patient|subject|visit|episode|^id$|_id$|^pid$)", re.I
)
_TIME_HINT = re.compile(
    r"(^time|charttime|timestamp|datetime|offset|minute|hour|^date|_time$|_at$)", re.I
)
_VARIABLE_HINT = re.compile(
    r"(variable|item|label|parameter|feature|concept|measurement|signal|^name$)", re.I
)
_VALUE_HINT = re.compile(r"(^value$|valuenum|result|measurement_value|^val$)", re.I)
_UNIT_HINT = re.compile(r"(^unit|valueuom|^uom$|units?$|unit_source_value)", re.I)
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
    n_events: int = 0
    n_intervals: int = 0
    mapping: dict[str, Any] = field(default_factory=dict)
    event_columns: list[str] = field(default_factory=list)
    dropped_columns: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unit_reports: list[Any] = field(default_factory=list)  # dea.suggest.units.UnitReport
    checks: list[Any] = field(default_factory=list)  # dea.quality.Check
    applied: list[str] = field(default_factory=list)  # ids of the checks whose fix was applied
    declined: list[str] = field(default_factory=list)  # fixable checks that were left alone

    def to_dict(self) -> dict[str, Any]:
        by = {"error": 0, "warning": 0, "info": 0}
        for c in self.checks:
            by[c.severity] = by.get(c.severity, 0) + 1
        return {
            **{k: v for k, v in self.__dict__.items() if k not in ("unit_reports", "checks")},
            "unit_reports": [r.to_dict() for r in self.unit_reports],
            "checks": [c.to_dict() for c in self.checks],
            "summary": {
                **by,
                "fixable": sum(1 for c in self.checks if c.action),
                "recommended": sum(1 for c in self.checks if c.action and c.recommended),
                "units_converted": sum(1 for r in self.unit_reports if r.converted),
                "units_split": sum(1 for r in self.unit_reports if r.split),
                "units_skipped": sum(1 for r in self.unit_reports if r.skipped),
            },
        }

    def save(self, cohort_dir: Path | str) -> Path:
        """Keep the report next to the cohort (``import.json``) so the UI can show it later."""
        out = Path(cohort_dir) / IMPORT_REPORT_FILE
        out.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")
        return out

    @staticmethod
    def load_dict(cohort_dir: Path | str) -> dict[str, Any] | None:
        file = Path(cohort_dir) / IMPORT_REPORT_FILE
        if not file.exists():
            return None
        return json.loads(file.read_text(encoding="utf-8"))

    def summary_lines(self) -> list[str]:
        lines = [
            f"source: {self.source}",
            f"format: {self.format}",
            f"encounters: {self.n_encounters}",
            f"variables: {self.n_variables}",
            f"observations: {self.n_observations}",
        ]
        if self.n_events:
            lines.append(f"events (string-valued): {self.n_events}")
        if self.n_intervals:
            lines.append(f"intervals (infusions, procedures): {self.n_intervals}")
        if self.mapping:
            lines.append("mapping: " + ", ".join(f"{k}={v}" for k, v in self.mapping.items() if v))
        if self.event_columns:
            lines.append("string columns kept as events: " + ", ".join(self.event_columns))
        if self.dropped_columns:
            lines.append("dropped columns: " + ", ".join(self.dropped_columns))
        lines.extend(f"warning: {w}" for w in self.warnings)
        lines.extend(f"units: {m}" for r in self.unit_reports if (m := r.message()))
        if self.applied:
            lines.append("fixes applied: " + ", ".join(self.applied))
        lines.extend(f"check [{c.severity}]: {c.title}" for c in self.checks)
        return lines


@dataclass
class Detection:
    """Best guess at how a source is laid out. Every field can be overridden on import."""

    source: Path
    format: Literal["dea", "csv_tree", "mimic", "fhir", "table", "unknown"]
    table_kind: Literal["long", "wide"] | None = None
    encounter_col: str | None = None
    time_col: str | None = None
    variable_col: str | None = None
    value_col: str | None = None
    unit_col: str | None = None
    time_unit: TimeUnit = "auto"
    columns: list[str] = field(default_factory=list)
    dtypes: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------- reading


def scan_table(path: Path | str) -> pl.LazyFrame:
    """Lazily scan CSV/TSV/Parquet/JSON-lines (gzip CSV included)."""
    p = Path(path)
    suffixes = "".join(p.suffixes).lower()
    if suffixes.endswith((".parquet", ".pq")):
        return pl.scan_parquet(p)
    if suffixes.endswith((".jsonl", ".ndjson")):
        return pl.scan_ndjson(p)
    return pl.scan_csv(
        p,
        separator="\t" if ".tsv" in suffixes else ",",
        infer_schema_length=10_000,
        try_parse_dates=True,
        null_values=["", "NA", "NaN", "nan", "NULL", "null", "None"],
    )


def read_table(path: Path | str, n_rows: int | None = None) -> pl.DataFrame:
    """Read a table (or its first ``n_rows``) into memory; used for previews and detection."""
    lf = scan_table(path)
    return (lf.head(n_rows) if n_rows else lf).collect()


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
    """Guess key columns and shape from a (sample of a) table."""
    det = Detection(
        source=Path(source),
        format="table",
        columns=df.columns,
        dtypes={c: str(t) for c, t in df.schema.items()},
    )
    cols = df.columns
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
    temporal = [c for c in cols if df.schema[c].is_temporal()]
    hinted_t = [c for c in cols if _TIME_HINT.search(c) and c != det.encounter_col]
    det.time_col = (hinted_t or temporal or [None])[0]
    if det.time_col is None:
        det.notes.append("no time column found")
    keys = (det.encounter_col, det.time_col)
    unit_hint = [c for c in cols if _UNIT_HINT.search(c) and c not in keys]
    det.unit_col = unit_hint[0] if unit_hint else None
    keys = (*keys, det.unit_col)
    var_hint = [c for c in cols if _VARIABLE_HINT.search(c) and c not in keys]
    val_hint = [c for c in cols if _VALUE_HINT.search(c) and c not in keys]
    rest = [c for c in cols if c not in keys]
    is_str = [df.schema[c] == pl.String for c in rest]
    if var_hint and val_hint:
        det.table_kind, det.variable_col, det.value_col = "long", var_hint[0], val_hint[0]
    elif len(rest) == 2 and is_str[0] and df.schema[rest[1]].is_numeric():
        det.table_kind, det.variable_col, det.value_col = "long", rest[0], rest[1]
    elif len(rest) == 2 and is_str[1] and df.schema[rest[0]].is_numeric():
        det.table_kind, det.variable_col, det.value_col = "long", rest[1], rest[0]
    else:
        det.table_kind = "wide"
        det.unit_col = None
    if det.time_col:
        det.time_unit = _guess_time_unit(df, det.time_col, det.encounter_col)
    return det


_DESCEND = 4  # how many directory levels below a dropped folder are searched for a layout


def _detect_below(root: Path, depth: int) -> Detection | None:
    """A recognizable layout somewhere below ``root``, at most ``depth`` levels down.

    A wget mirror of PhysioNet, a zip that unpacks into a wrapper folder or a dropped parent
    directory all put the real data a few levels down; the first MIMIC-IV, FHIR, v1 tree or
    DEA cohort found wins, a lone table only if nothing else turns up.
    """
    if depth <= 0:
        return None
    table: Detection | None = None
    for d in sorted(x for x in root.iterdir() if x.is_dir() and not x.name.startswith(".")):
        det = detect(d, _depth=depth - 1)
        if det.format in ("dea", "mimic", "fhir", "csv_tree"):
            return det
        if det.format == "table" and table is None:
            table = det
    return table


def detect(path: Path | str, *, _depth: int = _DESCEND) -> Detection:
    """Inspect a path and guess how to import it.

    For a directory that holds nothing recognizable itself, a few levels below are searched
    too, so a dropped parent folder or a PhysioNet mirror still resolves to the data inside.
    """
    p = Path(path)
    from dea.io.storage import is_cohort_dir  # local import to avoid a cycle at module load

    if p.is_dir():
        from dea.io.mimic import is_mimic_dir
        from dea.standards.fhir import is_fhir_source

        if is_cohort_dir(p):
            return Detection(source=p, format="dea")
        if is_mimic_dir(p):
            return Detection(source=p, format="mimic", time_unit="datetime")
        if (any(p.glob("*.ndjson")) or any(p.glob("*.json"))) and is_fhir_source(p):
            return Detection(source=p, format="fhir", time_unit="datetime")
        has_tree = (p / "static.csv").exists() or any(
            (d / "dynamic.csv").exists() for d in p.iterdir() if d.is_dir()
        )
        if has_tree:
            return Detection(source=p, format="csv_tree", time_unit="m")
        tables = sorted(x for x in p.iterdir() if x.suffix.lower() in (".csv", ".tsv", ".parquet"))
        if len(tables) == 1:
            return detect(tables[0])
        nested = _detect_below(p, _depth)
        if nested is not None:
            return nested
        seen = sorted(x.name + ("/" if x.is_dir() else "") for x in p.iterdir())
        shown = ", ".join(seen[:8]) + (", ..." if len(seen) > 8 else "")
        return Detection(
            source=p,
            format="unknown",
            notes=["directory holds no recognizable layout", f"it contains: {shown or 'nothing'}"],
        )
    if not p.exists():
        raise FileNotFoundError(p)
    if p.suffix.lower() in (".json", ".ndjson"):
        from dea.standards.fhir import is_fhir_source

        if is_fhir_source(p):
            return Detection(source=p, format="fhir", time_unit="datetime")
    try:
        sample = read_table(p, n_rows=_SAMPLE_ROWS)
    except Exception as exc:
        return Detection(source=p, format="unknown", notes=[f"could not read table: {exc}"])
    return detect_table(sample, p)


# -------------------------------------------------------------------------- conversion


def _to_duration(
    lf: pl.LazyFrame, time_col: str, encounter_col: str, unit: TimeUnit, report: ImportReport
) -> tuple[pl.LazyFrame, pl.LazyFrame | None]:
    """Add a ``time`` Duration column; returns the frame and, for timestamps, admission times."""
    dtype = lf.collect_schema()[time_col]
    if unit == "auto":
        sample = lf.select(encounter_col, time_col).head(_SAMPLE_ROWS).collect()
        unit = (
            "datetime" if dtype.is_temporal() else _guess_time_unit(sample, time_col, encounter_col)
        )
        report.warnings.append(f"time unit guessed as '{unit}' for column '{time_col}'")
    if dtype == pl.Duration:
        return lf.with_columns(pl.col(time_col).cast(LONG_SCHEMA[TIME]).alias(TIME)), None
    if unit == "datetime":
        if dtype == pl.String:
            lf = lf.with_columns(pl.col(time_col).str.to_datetime(strict=False))
        elif dtype == pl.Date:
            lf = lf.with_columns(pl.col(time_col).cast(pl.Datetime("us")))
        if not lf.collect_schema()[time_col].is_temporal():
            raise ValueError(f"column '{time_col}' cannot be parsed as datetime")
        admission = lf.group_by(encounter_col).agg(pl.col(time_col).min().alias(ADMISSION_TIME))
        lf = lf.join(admission, on=encounter_col, how="left").with_columns(
            (pl.col(time_col) - pl.col(ADMISSION_TIME)).cast(LONG_SCHEMA[TIME]).alias(TIME)
        )
        admission = admission.select(
            pl.col(encounter_col).cast(pl.String()).alias(ENCOUNTER_ID), ADMISSION_TIME
        )
        return lf.drop(ADMISSION_TIME), admission
    if dtype == pl.String:
        lf = lf.with_columns(pl.col(time_col).cast(pl.Float64(), strict=False))
    if not lf.collect_schema()[time_col].is_numeric():
        raise ValueError(f"column '{time_col}' is neither numeric nor datetime")
    seconds = (pl.col(time_col).cast(pl.Float64()) * _UNIT_SECONDS[unit]).round(0).cast(pl.Int64())
    return lf.with_columns(pl.duration(seconds=seconds).cast(LONG_SCHEMA[TIME]).alias(TIME)), None


def _classify_columns(
    lf: pl.LazyFrame, candidates: list[str], report: ImportReport
) -> tuple[list[str], list[str]]:
    """Split columns into numeric observations and string events, by dtype and a sample."""
    schema = lf.collect_schema()
    numeric, events = [], []
    sample = None
    for c in candidates:
        dtype = schema[c]
        if dtype.is_numeric() or dtype == pl.Boolean:
            numeric.append(c)
        elif dtype == pl.String:
            if sample is None:
                sample = lf.head(_SAMPLE_ROWS).collect()
            col = sample.get_column(c)
            casted = col.cast(pl.Float64(), strict=False)
            if col.null_count() < col.len() and casted.null_count() - col.null_count() <= max(
                1, col.len() // 100
            ):
                numeric.append(c)
            else:
                events.append(c)
        else:
            report.dropped_columns.append(c)
    report.event_columns = events
    return numeric, events


def wide_to_long(
    lf: pl.LazyFrame | pl.DataFrame,
    encounter_col: str,
    time_col: str,
    time_unit: TimeUnit = "auto",
    report: ImportReport | None = None,
    exclude: list[str] | None = None,
) -> tuple[pl.LazyFrame, pl.LazyFrame, pl.LazyFrame | None]:
    """Unpivot a wide table into (observations, events, admission times), all lazy."""
    report = report or ImportReport(source="<frame>", format="table")
    lf = lf.lazy().filter(pl.col(encounter_col).is_not_null())
    lf, admission = _to_duration(lf, time_col, encounter_col, time_unit, report)
    skip = {encounter_col, time_col, TIME, *(exclude or [])}
    candidates = [c for c in lf.collect_schema() if c not in skip and not c.startswith("Unnamed")]
    numeric, events = _classify_columns(lf, candidates, report)
    if not numeric and not events:
        raise ValueError("no variable columns found")
    key = [pl.col(encounter_col).cast(pl.String()).alias(ENCOUNTER_ID), pl.col(TIME)]
    valid = pl.col(VALUE).is_not_null() & pl.col(TIME).is_not_null()
    dynamic = (
        lf.select(*key, *[pl.col(c).cast(pl.Float64(), strict=False) for c in numeric])
        .unpivot(index=[ENCOUNTER_ID, TIME], on=numeric, variable_name=VARIABLE, value_name=VALUE)
        .filter(valid)
        .select(list(LONG_SCHEMA))
        if numeric
        else pl.LazyFrame(schema=LONG_SCHEMA)
    )
    event_lf = (
        lf.select(*key, *[pl.col(c).cast(pl.String()) for c in events])
        .unpivot(index=[ENCOUNTER_ID, TIME], on=events, variable_name=VARIABLE, value_name=VALUE)
        .filter(valid)
        .select(list(EVENT_SCHEMA))
        if events
        else pl.LazyFrame(schema=EVENT_SCHEMA)
    )
    return dynamic, event_lf, admission


def _finish(
    dynamic: pl.LazyFrame,
    events: pl.LazyFrame,
    static: pl.DataFrame | pl.LazyFrame | None,
    admission: pl.LazyFrame | None,
    name: str,
    report: ImportReport,
    source: str,
) -> Cohort:
    ids = collect(
        pl.concat([dynamic.select(ENCOUNTER_ID), events.select(ENCOUNTER_ID)]).unique()
    ).sort(ENCOUNTER_ID)
    if static is None:
        static = ids
    else:
        static = static.lazy().with_columns(pl.col(ENCOUNTER_ID).cast(pl.String())).collect()
        missing = ids.join(static, on=ENCOUNTER_ID, how="anti")
        if missing.height:
            report.warnings.append(
                f"{missing.height} encounter(s) have observations but no static row"
            )
            static = pl.concat([static, missing], how="diagonal_relaxed")
        orphan = static.join(ids, on=ENCOUNTER_ID, how="anti").height
        if orphan:
            report.warnings.append(f"{orphan} static row(s) have no observations")
    if admission is not None and ADMISSION_TIME not in static.columns:
        static = static.join(collect(admission), on=ENCOUNTER_ID, how="left")
    from dea.quality.sites import detect_site_column

    cohort = Cohort(
        static.sort(ENCOUNTER_ID),
        dynamic,
        events=events,
        config=CohortConfig(name=name, source=source, site_column=detect_site_column(static)),
    )
    if cohort.config.site_column:
        report.mapping["site"] = cohort.config.site_column
    report.n_encounters = len(cohort)
    counts = cohort.variable_counts()
    report.n_variables = counts.height
    report.n_observations = int(counts.get_column("n_observations").sum()) if counts.height else 0
    report.n_events = int(collect(events.select(pl.len())).item())
    return cohort


def import_wide(
    df: pl.DataFrame | pl.LazyFrame,
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
    lf = df.lazy()
    static_cols = static_cols or []
    if static_cols:
        pulled = (
            collect(
                lf.group_by(encounter_col).agg(
                    [pl.col(c).drop_nulls().first() for c in static_cols]
                )
            )
            .rename({encounter_col: ENCOUNTER_ID})
            .with_columns(pl.col(ENCOUNTER_ID).cast(pl.String()))
        )
        static = (
            pulled
            if static is None
            else static.with_columns(pl.col(ENCOUNTER_ID).cast(pl.String())).join(
                pulled, on=ENCOUNTER_ID, how="full", coalesce=True
            )
        )
    dynamic, events, admission = wide_to_long(
        lf, encounter_col, time_col, time_unit, report, static_cols
    )
    return _finish(dynamic, events, static, admission, name, report, str(source)), report


def import_long(
    df: pl.DataFrame | pl.LazyFrame,
    encounter_col: str,
    time_col: str,
    variable_col: str,
    value_col: str,
    time_unit: TimeUnit = "auto",
    static: pl.DataFrame | None = None,
    name: str = "cohort",
    source: str = "<frame>",
    unit_col: str | None = None,
) -> tuple[Cohort, ImportReport]:
    """Import a long table (one row per observation). Non-numeric values become events.

    With ``unit_col`` every row's unit is read and variables the dictionary recognizes are
    converted to their canonical unit (°F to °C, percent to fraction, ...); the report lists
    what changed.
    """
    report = ImportReport(
        source=str(source),
        format="table/long",
        mapping={
            "encounter": encounter_col,
            "time": time_col,
            "variable": variable_col,
            "value": value_col,
            "unit": unit_col,
            "time_unit": time_unit,
        },
    )
    lf = df.lazy().filter(pl.col(encounter_col).is_not_null() & pl.col(variable_col).is_not_null())
    lf, admission = _to_duration(lf, time_col, encounter_col, time_unit, report)
    extra = [pl.col(unit_col).cast(pl.String()).alias(UNIT)] if unit_col else []
    base = lf.select(
        pl.col(encounter_col).cast(pl.String()).alias(ENCOUNTER_ID),
        TIME,
        pl.col(variable_col).cast(pl.String()).alias(VARIABLE),
        pl.col(value_col).alias(VALUE),
        *extra,
    ).filter(pl.col(TIME).is_not_null() & pl.col(VALUE).is_not_null())
    if lf.collect_schema()[value_col].is_numeric():
        dynamic = base.with_columns(pl.col(VALUE).cast(pl.Float64()))
        events = pl.LazyFrame(schema=EVENT_SCHEMA)
    else:
        text = base.with_columns(pl.col(VALUE).cast(pl.String()))
        numeric = pl.col(VALUE).cast(pl.Float64(), strict=False)
        dynamic = text.filter(numeric.is_not_null()).with_columns(numeric)
        events = text.filter(numeric.is_null())
        report.event_columns = [f"{value_col} (non-numeric rows)"]
    units: dict[str, str] = {}
    codes: dict[str, str] = {}
    if unit_col:
        dynamic, units, codes = harmonize_units(dynamic, UNIT, report)
    cohort = _finish(
        dynamic.select(list(LONG_SCHEMA)),
        events.select(list(EVENT_SCHEMA)),
        static,
        admission,
        name,
        report,
        str(source),
    )
    cohort.config.variable_units.update(units)
    cohort.config.variable_codes.update(codes)
    return cohort, report


def harmonize_units(
    dynamic: pl.LazyFrame, unit_col: str, report: ImportReport, value_col: str = VALUE
) -> tuple[pl.LazyFrame, dict[str, str], dict[str, str]]:
    """Normalize per-row units of dictionary-known variables; returns frame, units, LOINC codes.

    The returned ``units`` map every variable to its display unit (the concept's unit when it
    was harmonized, otherwise the most frequent unit string in the data).
    """
    from dea.suggest.units import normalize_units
    from dea.suggest.vocab import load_dictionary

    d = load_dictionary()
    names = collect(dynamic.select(VARIABLE).unique()).get_column(VARIABLE).to_list()
    targets, codes = {}, {}
    for v in names:
        m = d.match(v)
        if m and m[0].ucum:
            targets[v] = (m[0].ucum, m[0].name)
            if m[0].loinc:
                codes[v] = m[0].loinc
    dynamic, report.unit_reports = normalize_units(dynamic, unit_col, targets, value_col=value_col)
    for rep in report.unit_reports:
        rep.label = d.by_name[targets[rep.variable][1]].unit
        if rep.skipped:  # not this concept after all: no canonical unit, no code
            targets.pop(rep.variable, None)
            codes.pop(rep.variable, None)
    majority = (
        collect(
            dynamic.filter(pl.col(unit_col).is_not_null() & (pl.col(unit_col) != ""))
            .group_by(VARIABLE, unit_col)
            .agg(pl.len().alias("n"))
        )
        .sort("n", descending=True)
        .unique(subset=[VARIABLE], keep="first")
    )
    units = {v: u for v, u in majority.select(VARIABLE, unit_col).iter_rows()}
    for v, (_, concept) in targets.items():
        units[v] = d.by_name[concept].unit
    return dynamic, units, codes


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
    dynamics, events = [], []
    for enc_dir in sorted(d for d in root.iterdir() if d.is_dir()):
        file = enc_dir / "dynamic.csv"
        if not file.exists():
            continue
        try:
            lf = scan_table(file)
            time_col = lf.collect_schema().names()[0]
            lf = lf.with_columns(pl.lit(enc_dir.name).alias("__encounter"))
            sub = ImportReport(source=str(file), format="csv_tree")
            dyn, evt, _ = wide_to_long(lf, "__encounter", time_col, time_unit, sub)
        except Exception as exc:
            report.warnings.append(f"{enc_dir.name}: {exc}")
            continue
        for c in sub.event_columns:
            if c not in report.event_columns:
                report.event_columns.append(c)
        dynamics.append(dyn)
        events.append(evt)
    if not dynamics:
        raise ValueError(f"no <id>/dynamic.csv files found under {root}")
    cohort = _finish(
        pl.concat(dynamics), pl.concat(events), static, None, name or root.name, report, str(root)
    )
    return cohort, report


def import_path(
    path: Path | str,
    name: str | None = None,
    detection: Detection | None = None,
    static_path: Path | str | None = None,
    checks: bool = True,
    fixes: list[str] | str | None = None,
    **overrides: Any,
) -> tuple[Cohort, ImportReport]:
    """Detect and import ``path``. Keyword overrides replace fields of the detection.

    With ``checks`` (the default) the data quality checks run on the imported cohort and
    land in ``report.checks`` before anything is written. ``fixes`` applies the actions of
    the named checks (a list of check ids, or ``"recommended"`` for every fix DEA marks as
    safe) before the cohort is returned; the checks then run again and ``report.checks``
    holds what is left, ``report.applied`` what was done.
    """
    progress.report("reading", Path(path).name)
    cohort, report = _import_path(path, name, detection, static_path, **overrides)
    if checks and report.format != "dea":
        from dea.quality import run_checks

        progress.report("running the checks")
        report.checks = run_checks(cohort, unit_reports=report.unit_reports)
        if fixes:
            cohort, report = apply_import_fixes(cohort, report, fixes)
    return cohort, report


def apply_import_fixes(
    cohort: Cohort, report: ImportReport, fixes: list[str] | str
) -> tuple[Cohort, ImportReport]:
    """Apply the fixes of ``report.checks`` named in ``fixes`` (ids, or ``"recommended"``)
    and run the checks again. Returns a new cohort and a new report; the inputs stay as they
    were, so an import that was checked once can be re-fixed any number of times."""
    import copy

    from dea.quality import run_checks
    from dea.quality.checks import apply_checks, choose

    chosen = choose(report.checks, fixes)
    if not chosen:
        return cohort, report
    out = copy.copy(report)
    progress.report("applying fixes", f"{len(chosen)} of them")
    fixed = apply_checks(cohort, chosen)
    out.applied = [c.id for c in chosen]
    progress.report("checking again")
    out.checks = run_checks(fixed, unit_reports=report.unit_reports)
    out.n_observations = fixed.summary().n_observations
    return fixed, out


def _import_path(
    path: Path | str,
    name: str | None,
    detection: Detection | None,
    static_path: Path | str | None,
    **overrides: Any,
) -> tuple[Cohort, ImportReport]:
    from dea.io.storage import load_cohort

    p = Path(path)
    det = detection or detect(p)
    static_cols = overrides.pop("static_cols", None)
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
    if det.format == "mimic":
        from dea.io.mimic import import_mimic

        return import_mimic(p, name=cohort_name)
    if det.format == "fhir":
        from dea.standards.fhir import import_fhir

        return import_fhir(p, name=cohort_name)
    if det.format != "table":
        raise ValueError(f"cannot import {p}: {'; '.join(det.notes) or 'unknown layout'}")
    lf = scan_table(p)
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
            lf,
            det.encounter_col,
            det.time_col,
            det.variable_col,
            det.value_col,
            det.time_unit,
            static,
            cohort_name,
            str(p),
            unit_col=det.unit_col or None,
        )
    return import_wide(
        lf, det.encounter_col, det.time_col, det.time_unit, static, static_cols, cohort_name, str(p)
    )

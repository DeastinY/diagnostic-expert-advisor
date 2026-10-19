"""Encounters and cohorts on Polars.

Data model
----------
Numeric observations are stored in **long format** with four columns::

    encounter_id (str) | time (Duration since admission) | variable (str) | value (f64)

Non-numeric observations (ventilation mode, drug names, free-text flags) use the same
shape with a string ``value`` and live in a separate ``events`` table.

Long format is what makes heterogeneous clinical data cheap to handle: encounters may
record different variables at different rates, nothing is padded, and the whole cohort
is scanned lazily from Parquet with Polars' streaming engine. Wide frames (one column per
variable) are produced on demand by :meth:`Encounter.wide` and :meth:`Cohort.wide`,
optionally resampled onto a regular grid.

Static, per-encounter information lives in one table keyed by ``encounter_id``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl

from dea.core.config import CohortConfig, ProcessingConfig, ValueFix
from dea.core.time import duration_seconds

if TYPE_CHECKING:
    from dea.compute.backend import Backend

ENCOUNTER_ID = "encounter_id"
TIME = "time"
VARIABLE = "variable"
VALUE = "value"

LONG_SCHEMA: dict[str, pl.DataType] = {
    ENCOUNTER_ID: pl.String(),
    TIME: pl.Duration(time_unit="us"),
    VARIABLE: pl.String(),
    VALUE: pl.Float64(),
}
EVENT_SCHEMA: dict[str, pl.DataType] = {**LONG_SCHEMA, VALUE: pl.String()}
START = "start"
END = "end"
# Interventions that last: infusions (value = rate), ventilation episodes, procedures.
INTERVAL_SCHEMA: dict[str, pl.DataType] = {
    ENCOUNTER_ID: pl.String(),
    START: pl.Duration(time_unit="us"),
    END: pl.Duration(time_unit="us"),
    VARIABLE: pl.String(),
    VALUE: pl.Float64(),
}

FEATURE_AGGS = ("min", "max", "mean", "first", "last", "count")
# stay statistics every feature table carries; a cached table missing one is recomputed
FEATURE_STAY_COLUMNS = (
    "los_hours",
    "n_observations",
    "n_variables",
    "pre_admission_hours",
    "n_pre_admission",
)
_HOURS_REF = re.compile(r'"([^"]+__hours)"')
FEATURE_VARIABLE_LIMIT = 200


class FilterError(ValueError):
    """Raised when a filter or tag expression cannot be evaluated."""


def collect(lf: pl.LazyFrame) -> pl.DataFrame:
    """Collect with the streaming engine so large cohorts never need to fit in memory."""
    return lf.collect(engine="streaming")


def ensure_schema(
    frame: pl.DataFrame | pl.LazyFrame, schema: dict[str, pl.DataType]
) -> pl.LazyFrame:
    """Validate and normalize a long frame to ``schema``."""
    lf = frame.lazy()
    have = lf.collect_schema()
    missing = [c for c in schema if c not in have]
    if missing:
        raise ValueError(f"long frame is missing columns {missing}; expected {list(schema)}")
    casts = [pl.col(n).cast(t) for n, t in schema.items() if have[n] != t]
    return (lf.with_columns(casts) if casts else lf).select(list(schema))


def ensure_long_schema(frame: pl.DataFrame | pl.LazyFrame) -> pl.LazyFrame:
    return ensure_schema(frame, LONG_SCHEMA)


def _grid_bucket(every_seconds: int) -> pl.Expr:
    """Floor ``time`` onto a grid of ``every_seconds``."""
    secs = pl.col(TIME).dt.total_seconds()
    return pl.duration(seconds=(secs // every_seconds) * every_seconds).alias(TIME)


def _apply_fill(frame: pl.DataFrame, processing: ProcessingConfig) -> pl.DataFrame:
    exprs = []
    for col in frame.columns:
        if col in (ENCOUNTER_ID, TIME):
            continue
        strategy = processing.fill_for(col)
        if strategy == "forward":
            exprs.append(pl.col(col).fill_null(strategy="forward"))
        elif strategy == "interpolate":
            exprs.append(pl.col(col).interpolate())
        elif strategy == "zero":
            exprs.append(pl.col(col).fill_null(0.0))
    return frame.with_columns(exprs) if exprs else frame


def long_to_wide(
    long: pl.DataFrame | pl.LazyFrame,
    processing: ProcessingConfig | None = None,
    variables: Iterable[str] | None = None,
    complete_grid: bool = True,
) -> pl.DataFrame:
    """Pivot long observations to a wide frame, applying resampling and fills.

    The result has ``encounter_id`` (if present in the input), ``time`` and one column per
    variable, sorted by encounter and time. With ``resample_every`` set, observations are
    aggregated per grid cell and, when ``complete_grid`` is true, empty cells between the
    first and last observation of each encounter are materialized so fills can act on them.
    Fills are applied per encounter.
    """
    processing = processing or ProcessingConfig()
    lf = long.lazy()
    schema = lf.collect_schema()
    has_id = ENCOUNTER_ID in schema
    keys = [ENCOUNTER_ID, TIME] if has_id else [TIME]

    if variables is not None:
        lf = lf.filter(pl.col(VARIABLE).is_in(list(variables)))
    if processing.drop_variables:
        lf = lf.filter(~pl.col(VARIABLE).is_in(processing.drop_variables))
    every = duration_seconds(processing.resample_every) if processing.resample_every else None
    if every:
        lf = lf.with_columns(_grid_bucket(every))
    agg = getattr(pl.col(VALUE), processing.aggregate)()
    df = collect(lf.group_by([*keys, VARIABLE]).agg(agg.alias(VALUE)))
    if df.is_empty():
        empty = {ENCOUNTER_ID: pl.String()} if has_id else {}
        return pl.DataFrame(schema={**empty, TIME: LONG_SCHEMA[TIME]})

    wide = df.pivot(on=VARIABLE, index=keys, values=VALUE, aggregate_function="first")

    if every and complete_grid:
        lo, hi = pl.col(TIME).min().alias("_lo"), pl.col(TIME).max().alias("_hi")
        bounds = df.group_by(ENCOUNTER_ID).agg(lo, hi) if has_id else df.select(lo, hi)
        grid = (
            bounds.with_columns(
                pl.int_ranges(
                    pl.col("_lo").dt.total_seconds(),
                    pl.col("_hi").dt.total_seconds() + every,
                    every,
                ).alias("_secs")
            )
            .explode("_secs")
            .select(
                *([pl.col(ENCOUNTER_ID)] if has_id else []),
                pl.duration(seconds=pl.col("_secs")).cast(LONG_SCHEMA[TIME]).alias(TIME),
            )
        )
        wide = grid.join(wide, on=keys, how="left")

    wide = wide.sort(keys)
    if has_id:
        parts = [
            _apply_fill(part, processing)
            for _, part in wide.group_by(ENCOUNTER_ID, maintain_order=True)
        ]
        wide = pl.concat(parts) if parts else wide
    else:
        wide = _apply_fill(wide, processing)
    var_cols = sorted(c for c in wide.columns if c not in keys)
    return wide.select([*keys, *var_cols])


@dataclass
class Encounter:
    """One hospital stay: static facts, numeric observations and string events."""

    id: str
    static: dict[str, Any]
    dynamic: pl.DataFrame
    events: pl.DataFrame = field(default_factory=lambda: pl.DataFrame(schema=EVENT_SCHEMA))
    intervals: pl.DataFrame = field(default_factory=lambda: pl.DataFrame(schema=INTERVAL_SCHEMA))
    config: CohortConfig | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.id = str(self.id)
        self.dynamic = ensure_schema(self.dynamic, LONG_SCHEMA).drop(ENCOUNTER_ID).collect()
        self.events = ensure_schema(self.events, EVENT_SCHEMA).drop(ENCOUNTER_ID).collect()
        self.intervals = ensure_schema(self.intervals, INTERVAL_SCHEMA).drop(ENCOUNTER_ID).collect()

    @property
    def variables(self) -> list[str]:
        return sorted(self.dynamic.get_column(VARIABLE).unique().to_list())

    @property
    def event_variables(self) -> list[str]:
        return sorted(self.events.get_column(VARIABLE).unique().to_list())

    @property
    def interval_variables(self) -> list[str]:
        return sorted(self.intervals.get_column(VARIABLE).unique().to_list())

    @property
    def n_observations(self) -> int:
        return self.dynamic.height

    @property
    def length_of_stay(self) -> timedelta:
        times = [
            t
            for t in (
                self.dynamic.get_column(TIME).max(),
                self.events.get_column(TIME).max(),
                self.intervals.get_column(END).max(),
            )
            if t
        ]
        return max(times) if times else timedelta(0)  # type: ignore[return-value]

    @property
    def los_hours(self) -> float:
        return self.length_of_stay.total_seconds() / 3600.0

    def series(self, variable: str) -> pl.DataFrame:
        """Observations of a single variable, sorted by time."""
        return self.dynamic.filter(pl.col(VARIABLE) == variable).select(TIME, VALUE).sort(TIME)

    def wide(
        self,
        every: str | None = None,
        fill: str | None = None,
        variables: Iterable[str] | None = None,
        processing: ProcessingConfig | None = None,
    ) -> pl.DataFrame:
        """Wide frame (``time`` plus one column per variable).

        ``every`` and ``fill`` override the cohort's processing config for this call.
        """
        base = processing or (self.config.processing if self.config else ProcessingConfig())
        return long_to_wide(self.dynamic, _override(base, every, fill), variables)

    def to_pandas(self, **kwargs: Any):  # noqa: ANN201 - pandas is optional
        """Wide frame as a pandas DataFrame indexed by time (requires ``dea[pandas]``)."""
        return self.wide(**kwargs).to_pandas().set_index(TIME)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "los_hours": round(self.los_hours, 2),
            "n_observations": self.n_observations,
            "n_variables": len(self.variables),
            **self.static,
        }


def _override(base: ProcessingConfig, every: str | None, fill: str | None) -> ProcessingConfig:
    updates: dict[str, Any] = {}
    if every is not None:
        updates["resample_every"] = every or None
    if fill is not None:
        updates["fill"] = fill
    return base.model_copy(update=updates) if updates else base


@dataclass(frozen=True)
class CohortSummary:
    name: str
    n_encounters: int
    n_variables: int
    n_observations: int
    los_hours_median: float
    los_hours_mean: float
    los_hours_min: float
    los_hours_max: float

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class Cohort:
    """A set of encounters: static table, lazy observations, lazy events and a config.

    Construct via :func:`dea.io.load_cohort`, the importers in :mod:`dea.io`, or directly
    from a static frame and long dynamic (and optionally events) frames.
    """

    FEATURES_CACHE = "_features.parquet"

    def __init__(
        self,
        static: pl.DataFrame,
        dynamic: pl.DataFrame | pl.LazyFrame,
        events: pl.DataFrame | pl.LazyFrame | None = None,
        config: CohortConfig | None = None,
        root: Path | None = None,
        name: str | None = None,
        _features: pl.DataFrame | None = None,
        _full: bool = True,
        intervals: pl.DataFrame | pl.LazyFrame | None = None,
    ) -> None:
        if ENCOUNTER_ID not in static.columns:
            raise ValueError(f"static table needs an '{ENCOUNTER_ID}' column")
        static = static.with_columns(pl.col(ENCOUNTER_ID).cast(pl.String()))
        if static.get_column(ENCOUNTER_ID).n_unique() != static.height:
            raise ValueError("static table has duplicate encounter ids")
        self.static: pl.DataFrame = static.select(
            ENCOUNTER_ID, *[c for c in static.columns if c != ENCOUNTER_ID]
        )
        self.dynamic: pl.LazyFrame = ensure_schema(dynamic, LONG_SCHEMA)
        self.events: pl.LazyFrame = ensure_schema(
            events if events is not None else pl.LazyFrame(schema=EVENT_SCHEMA), EVENT_SCHEMA
        )
        self.intervals: pl.LazyFrame = ensure_schema(
            intervals if intervals is not None else pl.LazyFrame(schema=INTERVAL_SCHEMA),
            INTERVAL_SCHEMA,
        )
        self.config: CohortConfig = config or CohortConfig(name=name or "cohort")
        if name:
            self.config.name = name
        self.root: Path | None = Path(root) if root else None
        self._features = _features
        self._full = _full  # False for subsets, which must not write the feature cache

    # ------------------------------------------------------------------ basics
    @property
    def name(self) -> str:
        return self.config.name

    @property
    def encounter_ids(self) -> list[str]:
        return self.static.get_column(ENCOUNTER_ID).to_list()

    def __len__(self) -> int:
        return self.static.height

    def __repr__(self) -> str:
        return f"Cohort(name={self.name!r}, encounters={len(self)})"

    def __contains__(self, encounter_id: str) -> bool:
        return str(encounter_id) in set(self.encounter_ids)

    def __iter__(self) -> Iterator[Encounter]:
        for eid in self.encounter_ids:
            yield self.get(eid)

    def __getitem__(self, encounter_id: str) -> Encounter:
        return self.get(encounter_id)

    def get(self, encounter_id: str) -> Encounter:
        eid = str(encounter_id)
        rows = self.static.filter(pl.col(ENCOUNTER_ID) == eid)
        if rows.is_empty():
            raise KeyError(f"unknown encounter {eid!r}")
        static = rows.row(0, named=True)
        static.pop(ENCOUNTER_ID, None)
        match = pl.col(ENCOUNTER_ID) == eid
        return Encounter(
            id=eid,
            static=static,
            dynamic=collect(self.dynamic.filter(match)),
            events=collect(self.events.filter(match)),
            intervals=collect(self.intervals.filter(match)),
            config=self.config,
        )

    def encounters(self, ids: Iterable[str] | None = None) -> list[Encounter]:
        """Materialize encounters (all by default) in one pass over the data."""
        wanted = [str(i) for i in ids] if ids is not None else self.encounter_ids
        static_rows = {
            row[ENCOUNTER_ID]: {k: v for k, v in row.items() if k != ENCOUNTER_ID}
            for row in self.static.filter(pl.col(ENCOUNTER_ID).is_in(wanted)).iter_rows(named=True)
        }
        unknown = [e for e in wanted if e not in static_rows]
        if unknown:
            raise KeyError(f"unknown encounter(s) {unknown[:5]!r}")
        match = pl.col(ENCOUNTER_ID).is_in(wanted)
        dyn = {k[0]: v for k, v in collect(self.dynamic.filter(match)).group_by(ENCOUNTER_ID)}
        evt = {k[0]: v for k, v in collect(self.events.filter(match)).group_by(ENCOUNTER_ID)}
        ivl = {k[0]: v for k, v in collect(self.intervals.filter(match)).group_by(ENCOUNTER_ID)}
        return [
            Encounter(
                id=eid,
                static=static_rows[eid],
                dynamic=dyn.get(eid, pl.DataFrame(schema=LONG_SCHEMA)),
                events=evt.get(eid, pl.DataFrame(schema=EVENT_SCHEMA)),
                intervals=ivl.get(eid, pl.DataFrame(schema=INTERVAL_SCHEMA)),
                config=self.config,
            )
            for eid in wanted
        ]

    def sorted_dynamic(self) -> pl.LazyFrame:
        """Observations ordered by encounter and time.

        Stored cohorts are already sorted on disk (each encounter sits in one time-sorted
        file), so this is free for them; in-memory cohorts are small enough to sort.
        """
        return self.dynamic if self.root else self.dynamic.sort(ENCOUNTER_ID, TIME)

    def variable_counts(self) -> pl.DataFrame:
        """``variable`` and ``n_observations`` for every numeric variable, most frequent first."""
        counts = self.dynamic.group_by(VARIABLE).agg(pl.len().alias("n_observations"))
        return collect(counts).sort(["n_observations", VARIABLE], descending=[True, False])

    @property
    def variables(self) -> list[str]:
        return sorted(self.variable_counts().get_column(VARIABLE).to_list())

    @property
    def event_variables(self) -> list[str]:
        return sorted(collect(self.events.select(pl.col(VARIABLE).unique())).get_column(VARIABLE))

    @property
    def interval_variables(self) -> list[str]:
        return sorted(
            collect(self.intervals.select(pl.col(VARIABLE).unique())).get_column(VARIABLE)
        )

    @property
    def static_columns(self) -> list[str]:
        return [c for c in self.static.columns if c != ENCOUNTER_ID]

    def default_static_column(self, max_categories: int = 30) -> str | None:
        """A sensible column to split charts by: the site column if there is one, otherwise
        the first categorical column with a handful of values that is not an identifier."""
        site = self.config.site_column
        if site and site in self.static.columns:
            return site
        for c in self.static_columns:
            low = c.lower()
            if low.endswith("id") or low in {"subject", "patient", "mrn", "name"}:
                continue
            s = self.static.get_column(c)
            if not (s.dtype.is_integer() or s.dtype in (pl.String, pl.Boolean, pl.Categorical)):
                continue
            if 2 <= s.n_unique() <= max_categories:
                return c
        return None

    def default_variable(self) -> str | None:
        """The first configured plot, else the most observed variable."""
        if self.config.plots:
            return self.config.plots[0]
        counts = self.variable_counts()
        return counts.get_column(VARIABLE)[0] if counts.height else None

    # ---------------------------------------------------------------- features
    def feature_variables(self) -> list[str]:
        """Variables that get per-encounter aggregates in :meth:`features`.

        ``config.feature_variables`` when set, otherwise the most observed variables up to
        :data:`FEATURE_VARIABLE_LIMIT`, so cohorts with thousands of item codes stay tractable.
        """
        if self.config.feature_variables:
            return list(self.config.feature_variables)
        return self.variable_counts().head(FEATURE_VARIABLE_LIMIT).get_column(VARIABLE).to_list()

    def _features_cache(self) -> Path | None:
        return self.root / "derived" / self.FEATURES_CACHE if self.root else None

    def features(self, refresh: bool = False) -> pl.DataFrame:
        """One row per encounter: static columns, stay statistics and per-variable aggregates.

        Variable aggregates are named ``<variable>__<agg>`` for ``agg`` in
        ``min, max, mean, first, last, count``. Filters and tags are evaluated on this table,
        and it doubles as a ready-made encounter-level feature matrix for ML. For stored
        cohorts the table is cached under ``derived/`` and reused until the cohort is saved.
        """
        if self._features is not None and not refresh:
            return self._features
        cache = self._features_cache()
        if cache and cache.exists() and not refresh and self._full:
            cached = pl.read_parquet(cache)
            # a table written by an older DEA lacks newer columns: compute it again
            if set(FEATURE_STAY_COLUMNS) <= set(cached.columns):
                self._features = cached
                return self._features
        self._features = self._compute_features()
        if cache and self._full:
            cache.parent.mkdir(parents=True, exist_ok=True)
            self._features.write_parquet(cache)
        return self._features

    def _compute_features(self) -> pl.DataFrame:
        stay = self.dynamic.group_by(ENCOUNTER_ID).agg(
            (pl.col(TIME).max().dt.total_seconds() / 3600.0).alias("los_hours"),
            pl.len().alias("n_observations"),
            pl.col(VARIABLE).n_unique().alias("n_variables"),
            # how far before admission the data reaches, and how much of it lies there
            (-(pl.col(TIME).min().dt.total_seconds()) / 3600.0)
            .clip(0)
            .alias("pre_admission_hours"),
            (pl.col(TIME) < pl.duration(seconds=0)).sum().alias("n_pre_admission"),
        )
        selected = self.feature_variables()
        per_var = collect(
            self.sorted_dynamic()
            .filter(pl.col(VARIABLE).is_in(selected))
            .group_by(ENCOUNTER_ID, VARIABLE)
            .agg(
                pl.col(VALUE).min().alias("min"),
                pl.col(VALUE).max().alias("max"),
                pl.col(VALUE).mean().alias("mean"),
                pl.col(VALUE).first().alias("first"),
                pl.col(VALUE).last().alias("last"),
                pl.col(VALUE).count().cast(pl.Float64()).alias("count"),
            )
        )
        hours = collect(
            self.intervals.group_by(ENCOUNTER_ID, VARIABLE).agg(
                ((pl.col(END) - pl.col(START)).dt.total_seconds().clip(0) / 3600.0)
                .sum()
                .alias("hours")
            )
        )
        if hours.height:  # <variable>__hours: total duration of an intervention per stay
            per_hours = hours.pivot(
                on=VARIABLE, index=ENCOUNTER_ID, values="hours", aggregate_function="first"
            )
            per_hours = per_hours.rename(
                {c: f"{c}__hours" for c in per_hours.columns if c != ENCOUNTER_ID}
            )
        else:
            per_hours = None
        if per_var.is_empty():
            wide_vars = pl.DataFrame(schema={ENCOUNTER_ID: pl.String()})
        else:
            wide_vars = (
                per_var.unpivot(
                    index=[ENCOUNTER_ID, VARIABLE], on=list(FEATURE_AGGS), variable_name="agg"
                )
                .with_columns((pl.col(VARIABLE) + pl.lit("__") + pl.col("agg")).alias("feature"))
                .pivot(on="feature", index=ENCOUNTER_ID, values=VALUE, aggregate_function="first")
            )
        if per_hours is not None:
            wide_vars = wide_vars.join(per_hours, on=ENCOUNTER_ID, how="full", coalesce=True)
            wide_vars = wide_vars.with_columns(
                [pl.col(c).fill_null(0.0) for c in per_hours.columns if c != ENCOUNTER_ID]
            )
        return (
            self.static.join(collect(stay), on=ENCOUNTER_ID, how="left")
            .join(wide_vars, on=ENCOUNTER_ID, how="left")
            .with_columns(
                pl.col("los_hours").fill_null(0.0),
                pl.col("pre_admission_hours").fill_null(0.0),
                pl.col("n_pre_admission").fill_null(0),
                pl.col("n_observations").fill_null(0),
                pl.col("n_variables").fill_null(0),
            )
            .sort(ENCOUNTER_ID)
        )

    def evaluate(self, where: str | pl.Expr) -> pl.Series:
        """Evaluate a rule expression against :meth:`features`; returns a boolean series."""
        feats = self.features()
        expr = pl.sql_expr(where) if isinstance(where, str) else where
        if isinstance(where, str):
            # an intervention nobody in this cohort received has no <x>__hours column; the
            # rule still means "zero hours of it", so supply the column rather than fail
            missing = [
                c
                for c in set(_HOURS_REF.findall(where))
                if c.endswith("__hours") and c not in feats.columns
            ]
            if missing:
                feats = feats.with_columns([pl.lit(0.0).alias(c) for c in missing])
        try:
            result = feats.select(expr.alias("_match")).get_column("_match")
        except Exception as exc:  # polars raises several error types
            raise FilterError(f"cannot evaluate {where!r}: {exc}") from exc
        if result.dtype != pl.Boolean:
            raise FilterError(f"expression {where!r} is not boolean")
        return result.fill_null(False)

    def tag_matrix(self) -> pl.DataFrame:
        """``encounter_id`` plus one boolean column per configured tag."""
        cols = [self.features().get_column(ENCOUNTER_ID)]
        cols += [self.evaluate(rule.where).alias(rule.label) for rule in self.config.tags]
        return pl.DataFrame(cols)

    def filter(self, where: str | pl.Expr | Iterable[str | pl.Expr]) -> Cohort:
        """New cohort restricted to encounters matching all given expressions."""
        exprs = [where] if isinstance(where, (str, pl.Expr)) else list(where)
        mask = None
        for e in exprs:
            m = self.evaluate(e)
            mask = m if mask is None else (mask & m)
        if mask is None:
            return self
        return self.select(self.features().filter(mask).get_column(ENCOUNTER_ID).to_list())

    def filter_by_labels(self, labels: Iterable[str]) -> Cohort:
        """Apply configured filters by label."""
        lookup = {r.label: r for r in self.config.filters}
        rules = []
        for label in labels:
            if label not in lookup:
                raise KeyError(f"unknown filter {label!r}")
            rules.append(lookup[label].where)
        return self.filter(rules) if rules else self

    def select(self, ids: Iterable[str]) -> Cohort:
        """New cohort containing only the given encounters (order of the static table kept)."""
        wanted = [str(i) for i in ids]
        match = pl.col(ENCOUNTER_ID).is_in(wanted)
        feats = self._features.filter(match) if self._features is not None else None
        return Cohort(
            self.static.filter(match),
            self.dynamic.filter(match),
            events=self.events.filter(match),
            intervals=self.intervals.filter(match),
            config=self.config,
            root=self.root,
            _features=feats,
            _full=False,
        )

    def rename_variables(self, mapping: dict[str, str]) -> Cohort:
        """New cohort with variables renamed (e.g. local codes to canonical names).

        Observations and events are renamed lazily; filters, tags, plots, labels and units in
        the config follow. Several source names may map to one canonical name. Save the
        result to persist it.
        """
        mapping = {k: v for k, v in mapping.items() if k != v}
        if not mapping:
            return self
        rename = pl.col(VARIABLE).replace(mapping).alias(VARIABLE)
        return Cohort(
            self.static,
            self.dynamic.with_columns(rename),
            events=self.events.with_columns(rename),
            intervals=self.intervals.with_columns(rename),
            config=self.config.renamed(mapping),
            root=self.root,
            _full=self._full,
        )

    def fix_values(
        self,
        variable: str,
        where: str,
        expr: str | None = None,
        description: str = "",
        encounter_where: str | None = None,
    ) -> Cohort:
        """Rewrite (or drop) the observations of ``variable`` selected by ``where``.

        ``where`` and ``expr`` are SQL over the ``value`` column, e.g.
        ``fix_values("Temperature", "value > 50", "(value - 32) / 1.8")`` repairs Fahrenheit
        rows; ``expr=None`` drops the selected rows; ``encounter_where`` is SQL over the static
        table that limits the fix to some encounters (one site, one export). The fix is
        recorded in the config so the provenance of stored values stays visible. Save the
        result to persist it.
        """
        hit = (pl.col(VARIABLE) == variable) & pl.sql_expr(where)
        if encounter_where:  # e.g. "site = 'Hospital B'": only that site's encounters
            ids = self.static.filter(pl.sql_expr(encounter_where)).get_column(ENCOUNTER_ID)
            hit = hit & pl.col(ENCOUNTER_ID).is_in(ids.implode())
        if expr is None:
            dynamic = self.dynamic.filter(~hit)
        else:
            dynamic = self.dynamic.with_columns(
                pl.when(hit).then(pl.sql_expr(expr)).otherwise(pl.col(VALUE)).alias(VALUE)
            )
        config = self.config.model_copy(deep=True)
        config.value_fixes.append(
            ValueFix(
                variable=variable,
                where=where,
                expr=expr,
                description=description,
                encounter_where=encounter_where,
            )
        )
        return Cohort(
            self.static,
            dynamic,
            events=self.events,
            intervals=self.intervals,
            config=config,
            root=self.root,
            _full=self._full,
        )

    def parse_numeric_events(self, variable: str) -> Cohort:
        """Turn an event variable whose values are numbers with text glued on ("37.2 C",
        "98 %") into observations; rows without a leading number stay events."""
        hit = pl.col(VARIABLE) == variable
        number = pl.col(VALUE).str.extract(r"^\s*([-+]?\d+(?:[.,]\d+)?)", 1)
        parsed = number.str.replace(",", ".").cast(pl.Float64(), strict=False)
        rows = self.events.filter(hit).with_columns(parsed.alias("_num"))
        numeric = rows.filter(pl.col("_num").is_not_null()).select(
            ENCOUNTER_ID, TIME, VARIABLE, pl.col("_num").alias(VALUE)
        )
        config = self.config.model_copy(deep=True)
        config.value_fixes.append(
            ValueFix(variable=variable, where="text", description="numbers parsed from text")
        )
        return Cohort(
            self.static,
            pl.concat([self.dynamic, numeric.select(list(LONG_SCHEMA))]),
            events=pl.concat(
                [self.events.filter(~hit), rows.filter(pl.col("_num").is_null()).drop("_num")]
            ),
            intervals=self.intervals,
            config=config,
            root=self.root,
            _full=self._full,
        )

    def clamp_to_admission(self, lead_hours: float = 24) -> Cohort:
        """Move the run-up, rows up to ``lead_hours`` before admission, to hour 0.

        A lab drawn in the emergency department then counts as the admission value, and
        features, trajectories and coverage, which start at admission, see it. A variable
        measured twice in the run-up lands twice on hour 0 (the duplicate check reports
        that). Rows further back are left alone; see :meth:`drop_before_admission`.
        """
        zero = pl.duration(seconds=0)
        run_up = (pl.col(TIME) < zero) & (pl.col(TIME) >= pl.duration(hours=-lead_hours))
        shift = pl.when(run_up).then(zero).otherwise(pl.col(TIME)).alias(TIME)
        config = self.config.model_copy(deep=True)
        config.value_fixes.append(
            ValueFix(
                variable="*",
                where=f"-{lead_hours:g}h <= time < 0",
                expr="time = 0",
                description=f"run-up of {lead_hours:g} h before admission moved to hour 0",
            )
        )
        return Cohort(
            self.static,
            self.dynamic.with_columns(shift),
            events=self.events.with_columns(shift),
            intervals=self.intervals,
            config=config,
            root=self.root,
            _full=self._full,
        )

    def drop_before_admission(self, lead_hours: float = 0) -> Cohort:
        """Drop observations and events more than ``lead_hours`` before admission.

        With the default of zero every negative time offset goes; a positive lead keeps the
        short run-up (labs drawn in the emergency department) and drops only what lies further
        back, such as a pre-admission weight or the alarm limits of an earlier stay.
        """
        keep = pl.col(TIME) >= pl.duration(hours=-lead_hours)
        config = self.config.model_copy(deep=True)
        if lead_hours:
            fix = ValueFix(
                variable="*",
                where=f"time < -{lead_hours:g}h",
                description=f"rows more than {lead_hours:g} h before admission removed",
            )
        else:
            fix = ValueFix(
                variable="*", where="time < 0", description="rows before admission removed"
            )
        config.value_fixes.append(fix)
        return Cohort(
            self.static,
            self.dynamic.filter(keep),
            events=self.events.filter(keep),
            intervals=self.intervals,
            config=config,
            root=self.root,
            _full=self._full,
        )

    def dedupe(self, how: str = "first") -> Cohort:
        """Resolve repeated (encounter, time, variable) observations.

        ``exact`` drops only rows that repeat the value as well, and is always safe. For keys
        recorded with *different* values, ``first`` and ``last`` keep that row, ``mean`` keeps
        one averaged row and ``drop`` removes every row of such a key.
        """
        key = [ENCOUNTER_ID, TIME, VARIABLE]
        dyn = self.dynamic
        if how == "exact":
            dynamic = dyn.unique(subset=[*key, VALUE], keep="first")
        elif how in ("first", "last"):
            dynamic = dyn.unique(subset=key, keep=how)
        elif how == "mean":
            dynamic = dyn.group_by(key).agg(pl.col(VALUE).mean()).select(list(LONG_SCHEMA))
        elif how == "drop":
            conflicts = (
                dyn.group_by(key)
                .agg(pl.col(VALUE).n_unique().alias("nv"))
                .filter(pl.col("nv") > 1)
                .select(key)
            )
            dynamic = dyn.join(conflicts, on=key, how="anti").unique(subset=key, keep="first")
        else:
            raise ValueError(f"unknown dedupe strategy {how!r}")
        described = {
            "exact": "exact duplicate rows removed",
            "first": "conflicting values at one time: first kept",
            "last": "conflicting values at one time: last kept",
            "mean": "conflicting values at one time: mean kept",
            "drop": "conflicting values at one time: all dropped",
        }
        config = self.config.model_copy(deep=True)
        config.value_fixes.append(
            ValueFix(variable="*", where=f"duplicate:{how}", description=described[how])
        )
        return Cohort(
            self.static,
            dynamic,
            events=self.events,
            intervals=self.intervals,
            config=config,
            root=self.root,
            _full=self._full,
        )

    def derive(
        self,
        path: Path | str,
        where: str | Iterable[str],
        name: str | None = None,
        description: str = "",
    ) -> Cohort:
        """Save the encounters matching ``where`` as a new cohort with lineage recorded.

        Only the selected encounters are streamed to the new directory; the config is copied
        with ``parent`` and ``derived_by`` set so sub-cohorts stay traceable.
        """
        rules = [where] if isinstance(where, str) else list(where)
        sub = self.filter(rules)
        config = self.config.model_copy(deep=True)
        config.name = name or Path(path).name
        config.description = description or f"Sub-cohort of {self.name}: {' AND '.join(rules)}"
        config.parent = str(self.root) if self.root else self.name
        config.derived_by = " AND ".join(rules)
        config.feature_variables = config.feature_variables
        out = Cohort(
            sub.static, sub.dynamic, events=sub.events, intervals=sub.intervals, config=config
        )
        out.save(path)
        return out

    def feature_catalog(self) -> list[dict[str, Any]]:
        """Describe every feature-table column for building filters: kind, range, categories."""
        feats = self.features()
        catalog: list[dict[str, Any]] = []
        for name, dtype in feats.schema.items():
            if name == ENCOUNTER_ID:
                continue
            col = feats.get_column(name)
            source = (
                "aggregate"
                if "__" in name
                else (
                    "stay" if name in ("los_hours", "n_observations", "n_variables") else "static"
                )
            )
            entry: dict[str, Any] = {"name": name, "source": source, "label": name}
            if "__" in name:
                var, agg = name.rsplit("__", 1)
                entry["label"] = f"{self.config.label(var)} ({agg})"
            if dtype.is_numeric():
                entry.update(kind="numeric", min=_f(col.min()), max=_f(col.max()))
            elif dtype == pl.Boolean:
                entry.update(kind="boolean")
            elif dtype.is_temporal():
                entry.update(kind="temporal")
            else:
                cats = col.drop_nulls().unique().sort().to_list()
                entry.update(kind="categorical", categories=[str(c) for c in cats[:50]])
            catalog.append(entry)
        return catalog

    def move_to_static(self, variables: Iterable[str]) -> Cohort:
        """Turn per-encounter-constant variables into static columns (first value per encounter)."""
        names = [v for v in variables]
        if not names:
            return self
        firsts = collect(
            self.sorted_dynamic()
            .filter(pl.col(VARIABLE).is_in(names))
            .group_by(ENCOUNTER_ID, VARIABLE)
            .agg(pl.col(VALUE).first())
        ).pivot(on=VARIABLE, index=ENCOUNTER_ID, values=VALUE)
        static = self.static.drop([c for c in names if c in self.static.columns]).join(
            firsts, on=ENCOUNTER_ID, how="left"
        )
        config = self.config.model_copy(deep=True)
        config.processing.drop_variables = [
            v for v in config.processing.drop_variables if v not in names
        ]
        config.plots = [v for v in config.plots if v not in names]
        return Cohort(
            static,
            self.dynamic.filter(~pl.col(VARIABLE).is_in(names)),
            events=self.events,
            intervals=self.intervals,
            config=config,
            root=self.root,
            _full=self._full,
        )

    # ----------------------------------------------------------------- outputs
    def summary(self) -> CohortSummary:
        feats = self.features()
        los = feats.get_column("los_hours")
        return CohortSummary(
            name=self.name,
            n_encounters=len(self),
            n_variables=len(self.variables),
            n_observations=int(feats.get_column("n_observations").sum()) if feats.height else 0,
            los_hours_median=float(los.median() or 0.0),
            los_hours_mean=float(los.mean() or 0.0),
            los_hours_min=float(los.min() or 0.0),
            los_hours_max=float(los.max() or 0.0),
        )

    def wide(
        self,
        every: str | None = None,
        fill: str | None = None,
        variables: Iterable[str] | None = None,
        with_static: bool = False,
        processing: ProcessingConfig | None = None,
    ) -> pl.DataFrame:
        """All encounters stacked in memory: ``encounter_id``, ``time``, one column per variable.

        This is the frame to hand to a model. For cohorts that do not fit in memory use
        :meth:`export_wide`, which writes the same frame bucket by bucket.
        """
        proc = _override(processing or self.config.processing, every, fill)
        wide = long_to_wide(self.dynamic, proc, variables)
        return wide.join(self.static, on=ENCOUNTER_ID, how="left") if with_static else wide

    def export_wide(
        self,
        path: Path | str,
        every: str | None = None,
        fill: str | None = None,
        variables: Iterable[str] | None = None,
        with_static: bool = False,
        processing: ProcessingConfig | None = None,
    ) -> Path:
        """Write :meth:`wide` as a Parquet dataset, one file per storage bucket.

        Memory use is bounded by the largest bucket (about 500 encounters), so this works
        for cohorts far larger than RAM. Read it back with ``pl.scan_parquet(path / "*.parquet")``.
        """
        out = Path(path)
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("part-*.parquet"):
            old.unlink()
        proc = _override(processing or self.config.processing, every, fill)
        files = sorted((self.root / "dynamic").rglob("*.parquet")) if self.root else []
        sources = [pl.scan_parquet(f) for f in files] or [self.dynamic]
        ids = set(self.encounter_ids)
        for i, src in enumerate(sources):
            part = long_to_wide(src.filter(pl.col(ENCOUNTER_ID).is_in(list(ids))), proc, variables)
            if part.is_empty():
                continue
            if with_static:
                part = part.join(self.static, on=ENCOUNTER_ID, how="left")
            part.write_parquet(out / f"part-{i:04d}.parquet")
        return out

    def trajectory(
        self,
        variable: str,
        every: str = "1h",
        by: str | None = None,
        max_hours: float | None = None,
        quantiles: tuple[float, float, float] = (0.25, 0.5, 0.75),
    ) -> pl.DataFrame:
        """Cohort-level course of one variable: quantiles per grid cell since admission.

        Every encounter contributes one value per cell (its mean in that cell), so frequently
        sampled encounters do not dominate. Columns: ``[by,] hours, lo, median, hi, n`` where
        ``n`` is the number of encounters with data in the cell. ``by`` names a static column
        to split the cohort by.
        """
        step = duration_seconds(every)
        # from admission on: the pre-admission run-up (emergency department labs) would
        # otherwise stretch every trajectory to the left
        lf = self.dynamic.filter(
            (pl.col(VARIABLE) == variable) & (pl.col(TIME) >= pl.duration(seconds=0))
        )
        if max_hours is not None:
            lf = lf.filter(pl.col(TIME) <= pl.duration(hours=max_hours))
        cell = ((pl.col(TIME).dt.total_seconds() // step) * step / 3600.0).alias("hours")
        keys = [ENCOUNTER_ID, "hours"]
        per_encounter = lf.with_columns(cell).group_by(keys).agg(pl.col(VALUE).mean())
        if by:
            if by not in self.static.columns:
                raise KeyError(f"unknown static column {by!r}")
            per_encounter = per_encounter.join(
                self.static.lazy().select(ENCOUNTER_ID, by), on=ENCOUNTER_ID, how="left"
            )
        groups = [by, "hours"] if by else ["hours"]
        lo, mid, hi = quantiles
        return collect(
            per_encounter.group_by(groups).agg(
                pl.col(VALUE).quantile(lo, interpolation="linear").alias("lo"),
                pl.col(VALUE).quantile(mid, interpolation="linear").alias("median"),
                pl.col(VALUE).quantile(hi, interpolation="linear").alias("hi"),
                pl.col(ENCOUNTER_ID).n_unique().alias("n"),
            )
        ).sort(groups)

    def coverage_matrix(
        self, every: str = "4h", max_hours: float = 168.0, variables: Iterable[str] | None = None
    ) -> pl.DataFrame:
        """Share of encounters with data per variable and grid cell since admission.

        The denominator per cell is the number of encounters still in the unit at the cell's
        start, so late cells are not diluted by discharged patients. Columns:
        ``variable, hours, n, at_risk, fraction``.
        """
        step = duration_seconds(every)
        lf = self.dynamic.filter(
            (pl.col(TIME) >= pl.duration(seconds=0))
            & (pl.col(TIME) <= pl.duration(hours=max_hours))
        )
        if variables is not None:
            lf = lf.filter(pl.col(VARIABLE).is_in(list(variables)))
        cell = ((pl.col(TIME).dt.total_seconds() // step) * step / 3600.0).alias("hours")
        cells = collect(
            lf.with_columns(cell)
            .group_by(VARIABLE, "hours")
            .agg(pl.col(ENCOUNTER_ID).n_unique().alias("n"))
        )
        los = self.features().get_column("los_hours")
        at_risk = pl.DataFrame(
            {"hours": sorted(cells.get_column("hours").unique().to_list())}
        ).with_columns(
            pl.col("hours")
            .map_elements(lambda h: int((los >= h).sum()), return_dtype=pl.Int64)
            .alias("at_risk")
        )
        return (
            cells.join(at_risk, on="hours")
            .with_columns((pl.col("n") / pl.col("at_risk").clip(1)).round(4).alias("fraction"))
            .sort(VARIABLE, "hours")
        )

    def distribution_bins(
        self, variables: Iterable[str] | None = None, bins: int = 24, sample: int = 300
    ) -> pl.DataFrame:
        """Histogram bins per variable from a sample of encounters, trimmed to the 1st-99th pct.

        Columns: ``variable, lo, hi, count``. Small enough to ship to a browser for any cohort.
        """
        ids = self.encounter_ids
        step = max(1, len(ids) // sample)
        lf = self.dynamic.filter(pl.col(ENCOUNTER_ID).is_in(ids[::step][:sample]))
        if variables is not None:
            lf = lf.filter(pl.col(VARIABLE).is_in(list(variables)))
        values = collect(lf.select(VARIABLE, VALUE))
        if values.is_empty():
            return pl.DataFrame(
                schema={
                    VARIABLE: pl.String(),
                    "lo": pl.Float64(),
                    "hi": pl.Float64(),
                    "count": pl.UInt32(),
                }
            )
        bounds = (
            values.group_by(VARIABLE)
            .agg(
                pl.col(VALUE).quantile(0.01).alias("_p01"),
                pl.col(VALUE).quantile(0.99).alias("_p99"),
            )
            .with_columns(
                pl.when(pl.col("_p99") > pl.col("_p01"))
                .then(pl.col("_p99") - pl.col("_p01"))
                .otherwise(1.0)
                .alias("_w")
            )
        )
        binned = (
            values.join(bounds, on=VARIABLE)
            .with_columns(
                ((pl.col(VALUE) - pl.col("_p01")) / pl.col("_w") * bins)
                .floor()
                .clip(0, bins - 1)
                .cast(pl.Int64())
                .alias("_b")
            )
            .group_by(VARIABLE, "_b", "_p01", "_w")
            .agg(pl.len().alias("count"))
            .with_columns(
                (pl.col("_p01") + pl.col("_b") * pl.col("_w") / bins).alias("lo"),
                (pl.col("_p01") + (pl.col("_b") + 1) * pl.col("_w") / bins).alias("hi"),
            )
            .select(VARIABLE, "lo", "hi", "count")
            .sort(VARIABLE, "lo")
        )
        return binned

    def to_pandas(self, **kwargs: Any):  # noqa: ANN201 - pandas is optional
        """:meth:`wide` as a pandas DataFrame with a (encounter_id, time) index."""
        return self.wide(**kwargs).to_pandas().set_index([ENCOUNTER_ID, TIME])

    # ----------------------------------------------------------------- compute
    def apply(
        self,
        fn: Callable[[Encounter], dict[str, Any] | pl.DataFrame | None],
        backend: Backend | None = None,
        name: str | None = None,
        ids: Iterable[str] | None = None,
    ) -> pl.DataFrame:
        """Run ``fn`` on every encounter through a compute backend and collect the results.

        ``fn`` returns either a dict (one row per encounter) or a DataFrame (any number of
        rows per encounter); an ``encounter_id`` column is added. When ``name`` is given and
        the cohort has a root directory, the result is stored under ``derived/<name>.parquet``
        and can be fetched later with :meth:`derived`.
        """
        from dea.compute.backend import SerialBackend, collect_results

        backend = backend or SerialBackend()
        encounters = self.encounters(ids)
        frame = collect_results([e.id for e in encounters], backend.map(fn, encounters))
        if name and self.root is not None:
            out = self.root / "derived"
            out.mkdir(parents=True, exist_ok=True)
            frame.write_parquet(out / f"{name}.parquet")
        return frame

    def derived(self, name: str) -> pl.DataFrame | None:
        if self.root is None:
            return None
        path = self.root / "derived" / f"{name}.parquet"
        return pl.read_parquet(path) if path.exists() else None

    def list_derived(self) -> list[str]:
        if self.root is None or not (self.root / "derived").exists():
            return []
        return sorted(
            p.stem for p in (self.root / "derived").glob("*.parquet") if not p.name.startswith("_")
        )

    # ----------------------------------------------------------------- storage
    def save(self, path: Path | None = None) -> Path:
        """Persist to ``path`` (or the cohort's root) in the DEA Parquet layout."""
        from dea.io.storage import save_cohort

        target = Path(path) if path else self.root
        if target is None:
            raise ValueError("no path given and cohort has no root")
        save_cohort(self, target)
        self.root = target
        self._features = None
        return target


def _f(x: Any) -> float | None:
    return None if x is None else float(x)

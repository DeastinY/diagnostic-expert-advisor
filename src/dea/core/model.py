"""Encounters and cohorts on Polars.

Data model
----------
Dynamic observations are stored in **long format** with four columns::

    encounter_id (str) | time (Duration since admission) | variable (str) | value (f64)

Long format is what makes heterogeneous clinical data cheap to handle: encounters may
record different variables at different rates, nothing is padded, and the whole cohort
can be scanned lazily from Parquet. Wide frames (one column per variable) are produced on
demand by :meth:`Encounter.wide` and :meth:`Cohort.wide`, optionally resampled onto a
regular grid.

Static, per-encounter information lives in one table keyed by ``encounter_id``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl

from dea.core.config import CohortConfig, ProcessingConfig
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

FEATURE_AGGS = ("min", "max", "mean", "first", "last", "count")


class FilterError(ValueError):
    """Raised when a filter or tag expression cannot be evaluated."""


def ensure_long_schema(frame: pl.DataFrame | pl.LazyFrame) -> pl.LazyFrame:
    """Validate and normalize a long frame to :data:`LONG_SCHEMA`."""
    lf = frame.lazy()
    schema = lf.collect_schema()
    missing = [c for c in LONG_SCHEMA if c not in schema]
    if missing:
        raise ValueError(f"long frame is missing columns {missing}; expected {list(LONG_SCHEMA)}")
    casts = []
    for name, dtype in LONG_SCHEMA.items():
        if schema[name] != dtype:
            casts.append(pl.col(name).cast(dtype))
    if casts:
        lf = lf.with_columns(casts)
    return lf.select(list(LONG_SCHEMA))


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

    if processing.resample_every:
        every = duration_seconds(processing.resample_every)
        lf = lf.with_columns(_grid_bucket(every))
        agg = getattr(pl.col(VALUE), processing.aggregate)()
        lf = lf.group_by([*keys, VARIABLE]).agg(agg.alias(VALUE))
    else:
        agg_fn = getattr(pl.col(VALUE), processing.aggregate)()
        lf = lf.group_by([*keys, VARIABLE]).agg(agg_fn.alias(VALUE))

    df = lf.collect()
    if df.is_empty():
        return pl.DataFrame(
            schema={**({ENCOUNTER_ID: pl.String()} if has_id else {}), TIME: LONG_SCHEMA[TIME]}
        )

    wide = df.pivot(on=VARIABLE, index=keys, values=VALUE, aggregate_function="first")

    if processing.resample_every and complete_grid:
        every = duration_seconds(processing.resample_every)
        bounds = (
            df.group_by(keys[:-1] or None).agg(
                pl.col(TIME).min().alias("_lo"), pl.col(TIME).max().alias("_hi")
            )
            if has_id
            else df.select(pl.col(TIME).min().alias("_lo"), pl.col(TIME).max().alias("_hi"))
        )
        bounds = bounds.with_columns(
            pl.int_ranges(
                pl.col("_lo").dt.total_seconds(),
                pl.col("_hi").dt.total_seconds() + every,
                every,
            ).alias("_secs")
        ).explode("_secs")
        grid = bounds.select(
            *([pl.col(ENCOUNTER_ID)] if has_id else []),
            pl.duration(seconds=pl.col("_secs")).cast(LONG_SCHEMA[TIME]).alias(TIME),
        )
        wide = grid.join(wide, on=keys, how="left")

    wide = wide.sort(keys)
    wide = _apply_fill(wide, processing)
    var_cols = sorted(c for c in wide.columns if c not in keys)
    return wide.select([*keys, *var_cols])


@dataclass
class Encounter:
    """One hospital stay: static facts plus long-format observations."""

    id: str
    static: dict[str, Any]
    dynamic: pl.DataFrame
    config: CohortConfig | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.id = str(self.id)
        lf = ensure_long_schema(self.dynamic)
        self.dynamic = lf.drop(ENCOUNTER_ID).collect()

    @property
    def variables(self) -> list[str]:
        return sorted(self.dynamic.get_column(VARIABLE).unique().to_list())

    @property
    def n_observations(self) -> int:
        return self.dynamic.height

    @property
    def length_of_stay(self) -> timedelta:
        if self.dynamic.is_empty():
            return timedelta(0)
        return self.dynamic.get_column(TIME).max()  # type: ignore[return-value]

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
        updates: dict[str, Any] = {}
        if every is not None:
            updates["resample_every"] = every
        if fill is not None:
            updates["fill"] = fill
        proc = base.model_copy(update=updates) if updates else base
        return long_to_wide(self.dynamic, proc, variables)

    def to_pandas(self, **kwargs: Any):  # noqa: ANN201 - pandas is optional
        """Wide frame as a pandas DataFrame indexed by time (requires ``dea[pandas]``)."""
        wide = self.wide(**kwargs)
        return wide.to_pandas().set_index(TIME)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "los_hours": round(self.los_hours, 2),
            "n_observations": self.n_observations,
            "n_variables": len(self.variables),
            **self.static,
        }


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
    """A set of encounters with shared static table, lazy dynamic data and a config.

    Construct via :func:`dea.io.load_cohort`, the importers in :mod:`dea.io`, or directly
    from a static frame and a long dynamic frame.
    """

    def __init__(
        self,
        static: pl.DataFrame,
        dynamic: pl.DataFrame | pl.LazyFrame,
        config: CohortConfig | None = None,
        root: Path | None = None,
        name: str | None = None,
        _features: pl.DataFrame | None = None,
    ) -> None:
        if ENCOUNTER_ID not in static.columns:
            raise ValueError(f"static table needs an '{ENCOUNTER_ID}' column")
        static = static.with_columns(pl.col(ENCOUNTER_ID).cast(pl.String()))
        if static.get_column(ENCOUNTER_ID).n_unique() != static.height:
            raise ValueError("static table has duplicate encounter ids")
        self.static: pl.DataFrame = static.select(
            ENCOUNTER_ID, *[c for c in static.columns if c != ENCOUNTER_ID]
        )
        self.dynamic: pl.LazyFrame = ensure_long_schema(dynamic)
        self.config: CohortConfig = config or CohortConfig(name=name or "cohort")
        if name:
            self.config.name = name
        self.root: Path | None = Path(root) if root else None
        self._features = _features

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
        dynamic = self.dynamic.filter(pl.col(ENCOUNTER_ID) == eid).collect()
        return Encounter(id=eid, static=static, dynamic=dynamic, config=self.config)

    def encounters(self, ids: Iterable[str] | None = None) -> list[Encounter]:
        """Materialize encounters (all by default) in a single pass over the data."""
        wanted = [str(i) for i in ids] if ids is not None else self.encounter_ids
        wanted_set = set(wanted)
        frame = self.dynamic.filter(pl.col(ENCOUNTER_ID).is_in(wanted)).collect()
        parts = {eid: part for (eid,), part in frame.group_by(ENCOUNTER_ID)}
        static_rows = {
            row[ENCOUNTER_ID]: {k: v for k, v in row.items() if k != ENCOUNTER_ID}
            for row in self.static.filter(pl.col(ENCOUNTER_ID).is_in(wanted)).iter_rows(named=True)
        }
        out = []
        for eid in wanted:
            if eid not in wanted_set or eid not in static_rows:
                raise KeyError(f"unknown encounter {eid!r}")
            dyn = parts.get(eid)
            if dyn is None:
                dyn = pl.DataFrame(schema=LONG_SCHEMA)
            out.append(Encounter(id=eid, static=static_rows[eid], dynamic=dyn, config=self.config))
        return out

    @property
    def variables(self) -> list[str]:
        return sorted(
            self.dynamic.select(pl.col(VARIABLE).unique()).collect().get_column(VARIABLE).to_list()
        )

    @property
    def static_columns(self) -> list[str]:
        return [c for c in self.static.columns if c != ENCOUNTER_ID]

    # ---------------------------------------------------------------- features
    def features(self, refresh: bool = False) -> pl.DataFrame:
        """One row per encounter: static columns, stay statistics and per-variable aggregates.

        Variable aggregates are named ``<variable>__<agg>`` for ``agg`` in
        ``min, max, mean, first, last, count``. Filters and tags are evaluated on this table,
        and it doubles as a ready-made encounter-level feature matrix for ML.
        """
        if self._features is not None and not refresh:
            return self._features
        stay = self.dynamic.group_by(ENCOUNTER_ID).agg(
            (pl.col(TIME).max().dt.total_seconds() / 3600.0).alias("los_hours"),
            pl.len().alias("n_observations"),
            pl.col(VARIABLE).n_unique().alias("n_variables"),
        )
        per_var = (
            self.dynamic.sort(TIME)
            .group_by(ENCOUNTER_ID, VARIABLE)
            .agg(
                pl.col(VALUE).min().alias("min"),
                pl.col(VALUE).max().alias("max"),
                pl.col(VALUE).mean().alias("mean"),
                pl.col(VALUE).first().alias("first"),
                pl.col(VALUE).last().alias("last"),
                pl.col(VALUE).count().cast(pl.Float64()).alias("count"),
            )
            .collect()
        )
        if per_var.is_empty():
            wide_vars = pl.DataFrame({ENCOUNTER_ID: []}, schema={ENCOUNTER_ID: pl.String()})
        else:
            melted = per_var.unpivot(
                index=[ENCOUNTER_ID, VARIABLE], on=list(FEATURE_AGGS), variable_name="agg"
            ).with_columns((pl.col(VARIABLE) + pl.lit("__") + pl.col("agg")).alias("feature"))
            wide_vars = melted.pivot(
                on="feature", index=ENCOUNTER_ID, values=VALUE, aggregate_function="first"
            )
        feats = (
            self.static.join(stay.collect(), on=ENCOUNTER_ID, how="left")
            .join(wide_vars, on=ENCOUNTER_ID, how="left")
            .with_columns(
                pl.col("los_hours").fill_null(0.0),
                pl.col("n_observations").fill_null(0),
                pl.col("n_variables").fill_null(0),
            )
            .sort(ENCOUNTER_ID)
        )
        self._features = feats
        return feats

    def evaluate(self, where: str | pl.Expr) -> pl.Series:
        """Evaluate a rule expression against :meth:`features`; returns a boolean series."""
        feats = self.features()
        expr = pl.sql_expr(where) if isinstance(where, str) else where
        try:
            result = feats.select(expr.alias("_match")).get_column("_match")
        except Exception as exc:  # polars raises several error types
            raise FilterError(f"cannot evaluate {where!r}: {exc}") from exc
        if result.dtype != pl.Boolean:
            raise FilterError(f"expression {where!r} is not boolean")
        return result.fill_null(False)

    def tag_matrix(self) -> pl.DataFrame:
        """``encounter_id`` plus one boolean column per configured tag."""
        feats = self.features()
        cols = [feats.get_column(ENCOUNTER_ID)]
        for rule in self.config.tags:
            cols.append(self.evaluate(rule.where).alias(rule.label))
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
        ids = self.features().filter(mask).get_column(ENCOUNTER_ID).to_list()
        return self.select(ids)

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
        static = self.static.filter(pl.col(ENCOUNTER_ID).is_in(wanted))
        dynamic = self.dynamic.filter(pl.col(ENCOUNTER_ID).is_in(wanted))
        feats = (
            self._features.filter(pl.col(ENCOUNTER_ID).is_in(wanted))
            if self._features is not None
            else None
        )
        return Cohort(static, dynamic, config=self.config, root=self.root, _features=feats)

    # ----------------------------------------------------------------- outputs
    def summary(self) -> CohortSummary:
        feats = self.features()
        los = feats.get_column("los_hours")
        n_obs = int(feats.get_column("n_observations").sum()) if feats.height else 0
        return CohortSummary(
            name=self.name,
            n_encounters=len(self),
            n_variables=len(self.variables),
            n_observations=n_obs,
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
        """All encounters stacked: ``encounter_id``, ``time`` and one column per variable.

        This is the frame to hand to a model. With ``with_static`` the static columns are
        repeated on every row.
        """
        base = processing or self.config.processing
        updates: dict[str, Any] = {}
        if every is not None:
            updates["resample_every"] = every
        if fill is not None:
            updates["fill"] = fill
        proc = base.model_copy(update=updates) if updates else base
        wide = long_to_wide(self.dynamic, proc, variables)
        if with_static:
            wide = wide.join(self.static, on=ENCOUNTER_ID, how="left")
        return wide

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
        results = backend.map(fn, encounters)
        frame = collect_results([e.id for e in encounters], results)
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
        return sorted(p.stem for p in (self.root / "derived").glob("*.parquet"))

    # ----------------------------------------------------------------- storage
    def save(self, path: Path | None = None) -> Path:
        """Persist to ``path`` (or the cohort's root) in the DEA Parquet layout."""
        from dea.io.storage import save_cohort

        target = Path(path) if path else self.root
        if target is None:
            raise ValueError("no path given and cohort has no root")
        save_cohort(self, target)
        self.root = target
        return target

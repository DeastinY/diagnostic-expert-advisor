"""Profile a cohort without any model in the loop.

The profile answers the questions a researcher asks first: how often is each variable
measured, how complete is it, what range does it take, which columns look like identifiers
or constants, and what grid should the data be resampled onto. It is also the *only* thing
the optional AI advisor is allowed to see: aggregates, never patient rows.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import polars as pl

from dea.core.model import ENCOUNTER_ID, TIME, VALUE, VARIABLE, Cohort
from dea.core.time import format_duration, snap_to_ladder

MIN_CATEGORY_COUNT = 5  # categories shown to the advisor need at least this many encounters
MAX_CATEGORIES = 10


@dataclass
class VariableProfile:
    name: str
    n_observations: int
    n_encounters: int
    coverage: float
    min: float | None
    p01: float | None
    median: float | None
    mean: float | None
    p99: float | None
    max: float | None
    std: float | None
    n_unique: int
    median_interval_s: float | None
    flags: list[str] = field(default_factory=list)

    @property
    def interval_label(self) -> str:
        return format_duration(self.median_interval_s) if self.median_interval_s else "n/a"


@dataclass
class StaticColumnProfile:
    name: str
    dtype: str
    kind: str  # numeric | categorical | identifier | text | boolean | other
    n_missing: int
    n_unique: int
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    top_values: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class SamplingProfile:
    median_interval_s: float | None
    p10_interval_s: float | None
    p90_interval_s: float | None
    regular_fraction: float | None
    suggested_resample: str | None


@dataclass
class CohortProfile:
    name: str
    n_encounters: int
    n_variables: int
    n_observations: int
    los_hours_min: float
    los_hours_median: float
    los_hours_mean: float
    los_hours_max: float
    sampling: SamplingProfile
    variables: list[VariableProfile]
    static_columns: list[StaticColumnProfile]
    warnings: list[str]
    generated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def variables_frame(self) -> pl.DataFrame:
        rows = [
            {**asdict(v), "flags": ", ".join(v.flags), "interval": v.interval_label}
            for v in self.variables
        ]
        return pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()

    def static_frame(self) -> pl.DataFrame:
        rows = []
        for s in self.static_columns:
            row = asdict(s)
            row["top_values"] = ", ".join(f"{v} ({n})" for v, n in s.top_values)
            rows.append(row)
        return pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()

    def to_prompt_dict(self) -> dict[str, Any]:
        """The privacy-reduced payload shared with an AI advisor.

        Contains cohort-level counts, sampling statistics and per-column aggregates only.
        No encounter identifiers, no timestamps, no individual values; categorical labels
        appear only for low-cardinality static columns where every shown category covers at
        least :data:`MIN_CATEGORY_COUNT` encounters.
        """

        def r(x: float | None, nd: int = 3) -> float | None:
            return None if x is None else round(float(x), nd)

        return {
            "cohort": {
                "n_encounters": self.n_encounters,
                "n_variables": self.n_variables,
                "n_observations": self.n_observations,
                "length_of_stay_hours": {
                    "min": r(self.los_hours_min, 1),
                    "median": r(self.los_hours_median, 1),
                    "mean": r(self.los_hours_mean, 1),
                    "max": r(self.los_hours_max, 1),
                },
            },
            "sampling": asdict(self.sampling),
            "variables": [
                {
                    "name": v.name,
                    "n_observations": v.n_observations,
                    "coverage": r(v.coverage),
                    "range": [r(v.min), r(v.max)],
                    "p01_p99": [r(v.p01), r(v.p99)],
                    "median": r(v.median),
                    "mean": r(v.mean),
                    "std": r(v.std),
                    "n_unique": v.n_unique,
                    "median_interval": v.interval_label,
                    "flags": v.flags,
                }
                for v in self.variables
            ],
            "static_columns": [
                {
                    "name": s.name,
                    "dtype": s.dtype,
                    "kind": s.kind,
                    "n_missing": s.n_missing,
                    "n_unique": s.n_unique,
                    "range": [r(s.min), r(s.max)] if s.kind == "numeric" else None,
                    "categories": [v for v, _ in s.top_values] if s.top_values else None,
                }
                for s in self.static_columns
            ],
            "warnings": self.warnings,
        }


def _variable_profiles(
    cohort: Cohort, n_encounters: int
) -> tuple[list[VariableProfile], pl.DataFrame]:
    sorted_dyn = cohort.dynamic.sort(ENCOUNTER_ID, VARIABLE, TIME)
    gaps = sorted_dyn.with_columns(
        pl.col(TIME).diff().over([ENCOUNTER_ID, VARIABLE]).dt.total_seconds().alias("_gap")
    )
    per_encounter_unique = (
        sorted_dyn.group_by(ENCOUNTER_ID, VARIABLE)
        .agg(pl.col(VALUE).n_unique().alias("_nu"), pl.len().alias("_n"))
        .group_by(VARIABLE)
        .agg(
            pl.col("_nu").max().alias("_max_unique_per_encounter"),
            pl.col("_n").max().alias("_max_obs_per_encounter"),
        )
    )
    stats = (
        gaps.group_by(VARIABLE)
        .agg(
            pl.len().alias("n_observations"),
            pl.col(ENCOUNTER_ID).n_unique().alias("n_encounters"),
            pl.col(VALUE).min().alias("min"),
            pl.col(VALUE).quantile(0.01).alias("p01"),
            pl.col(VALUE).median().alias("median"),
            pl.col(VALUE).mean().alias("mean"),
            pl.col(VALUE).quantile(0.99).alias("p99"),
            pl.col(VALUE).max().alias("max"),
            pl.col(VALUE).std().alias("std"),
            pl.col(VALUE).n_unique().alias("n_unique"),
            pl.col("_gap").median().alias("median_interval_s"),
            ((pl.col(VALUE) - pl.col(VALUE).round(0)).abs().max() < 1e-9).alias("_integer"),
        )
        .join(per_encounter_unique, on=VARIABLE, how="left")
        .sort(VARIABLE)
        .collect()
    )
    profiles = []
    for row in stats.iter_rows(named=True):
        flags: list[str] = []
        coverage = row["n_encounters"] / n_encounters if n_encounters else 0.0
        if row["n_unique"] <= 1:
            flags.append("constant")
        elif (
            row["min"] is not None and row["min"] >= 0 and row["max"] <= 1 and row["n_unique"] <= 2
        ):
            flags.append("binary")
        if (
            row["n_unique"] > 1
            and row["_max_unique_per_encounter"] == 1
            and row["_max_obs_per_encounter"] > 1
        ):
            flags.append("constant_per_encounter")
        if row["_integer"]:
            flags.append("integer")
        if coverage < 0.1:
            flags.append("low_coverage")
        if row["median_interval_s"] is not None and row["median_interval_s"] >= 86400:
            flags.append("sparse")
        if (
            row["_integer"]
            and row["n_observations"] > 10
            and row["n_unique"] == row["n_observations"]
        ):
            flags.append("suspected_identifier")
        profiles.append(
            VariableProfile(
                name=row[VARIABLE],
                n_observations=int(row["n_observations"]),
                n_encounters=int(row["n_encounters"]),
                coverage=round(coverage, 4),
                min=row["min"],
                p01=row["p01"],
                median=row["median"],
                mean=row["mean"],
                p99=row["p99"],
                max=row["max"],
                std=row["std"],
                n_unique=int(row["n_unique"]),
                median_interval_s=row["median_interval_s"],
                flags=flags,
            )
        )
    gap_values = gaps.select("_gap").drop_nulls().collect().get_column("_gap")
    return profiles, gap_values.to_frame()


def _sampling_profile(gaps: pl.Series) -> SamplingProfile:
    if gaps.is_empty():
        return SamplingProfile(None, None, None, None, None)
    median = float(gaps.median())
    rounded = gaps.round(0)
    mode_count = rounded.value_counts(sort=True).get_column("count")[0]
    regular = float(mode_count) / gaps.len()
    return SamplingProfile(
        median_interval_s=median,
        p10_interval_s=float(gaps.quantile(0.1)),
        p90_interval_s=float(gaps.quantile(0.9)),
        regular_fraction=round(regular, 4),
        suggested_resample=snap_to_ladder(median),
    )


def _static_profiles(static: pl.DataFrame) -> list[StaticColumnProfile]:
    out = []
    n = static.height
    for name in static.columns:
        if name == ENCOUNTER_ID:
            continue
        s = static.get_column(name)
        dtype = s.dtype
        n_missing = int(s.null_count())
        n_unique = int(s.drop_nulls().n_unique())
        prof = StaticColumnProfile(
            name=name, dtype=str(dtype), kind="other", n_missing=n_missing, n_unique=n_unique
        )
        if dtype.is_numeric():
            prof.kind = "numeric"
            if n_unique == n and n > 20 and dtype.is_integer():
                prof.kind = "identifier"
            prof.min = _f(s.min())
            prof.max = _f(s.max())
            prof.mean = _f(s.mean())
        elif dtype == pl.Boolean:
            prof.kind = "boolean"
        elif dtype in (pl.String, pl.Categorical, pl.Enum):
            if n_unique == n and n > 20:
                prof.kind = "identifier"
            elif n_unique <= 20:
                prof.kind = "categorical"
                counts = (
                    s.drop_nulls()
                    .cast(pl.String())
                    .value_counts(sort=True)
                    .filter(pl.col("count") >= MIN_CATEGORY_COUNT)
                    .head(MAX_CATEGORIES)
                )
                prof.top_values = [(str(v), int(c)) for v, c in counts.iter_rows()]
            else:
                prof.kind = "text"
        elif dtype.is_temporal():
            prof.kind = "temporal"
        out.append(prof)
    return out


def _f(x: Any) -> float | None:
    return None if x is None else float(x)


def profile_cohort(cohort: Cohort) -> CohortProfile:
    """Compute a :class:`CohortProfile` for ``cohort``."""
    n_enc = len(cohort)
    summary = cohort.summary()
    variables, gaps_frame = _variable_profiles(cohort, n_enc)
    sampling = _sampling_profile(
        gaps_frame.get_column("_gap") if gaps_frame.height else pl.Series([], dtype=pl.Int64)
    )
    statics = _static_profiles(cohort.static)

    warnings: list[str] = []
    feats = cohort.features()
    empty = feats.filter(pl.col("n_observations") == 0).height
    if empty:
        warnings.append(f"{empty} encounter(s) have no observations")
    for v in variables:
        if "suspected_identifier" in v.flags:
            warnings.append(f"'{v.name}' looks like an identifier, not a measurement")
        if "constant" in v.flags:
            warnings.append(f"'{v.name}' is constant and carries no information")
        if "constant_per_encounter" in v.flags:
            warnings.append(
                f"'{v.name}' never changes within an encounter; it is a static attribute "
                "recorded as a time series"
            )
    low = [v.name for v in variables if "low_coverage" in v.flags]
    if low:
        warnings.append(f"{len(low)} variable(s) are present in fewer than 10% of encounters")
    for s in statics:
        if s.kind == "text":
            warnings.append(f"static column '{s.name}' is free text; check for identifying content")
    if sampling.regular_fraction is not None and sampling.regular_fraction < 0.5:
        warnings.append("sampling is irregular; resample before modelling")

    return CohortProfile(
        name=cohort.name,
        n_encounters=n_enc,
        n_variables=len(variables),
        n_observations=summary.n_observations,
        los_hours_min=summary.los_hours_min,
        los_hours_median=summary.los_hours_median,
        los_hours_mean=summary.los_hours_mean,
        los_hours_max=summary.los_hours_max,
        sampling=sampling,
        variables=variables,
        static_columns=statics,
        warnings=warnings,
    )

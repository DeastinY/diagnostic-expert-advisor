"""Profile a cohort without any model in the loop.

The profile answers the questions a researcher asks first: how often is each variable
measured, how complete is it, what range does it take, which columns look like identifiers
or constants, and what grid should the data be resampled onto. It is also the *only* thing
the optional AI advisor is allowed to see: aggregates, never patient rows.

Counts, coverage, min, max, mean and std stream over the whole cohort. Quantiles, distinct
counts and sampling intervals need whole groups in memory and are computed on a
deterministic sample of encounters (``sampling.sampled_encounters`` says how many).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import polars as pl

from dea.core.model import ENCOUNTER_ID, TIME, VALUE, VARIABLE, Cohort, collect
from dea.core.time import format_duration, snap_to_ladder

MIN_CATEGORY_COUNT = 5  # categories shown to the advisor need at least this many encounters
MAX_CATEGORIES = 10
INTERVAL_SAMPLE = 300  # encounters used for sampling-interval statistics


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
class IntervalProfile:
    name: str
    n_episodes: int
    n_encounters: int
    coverage: float
    median_hours: float | None
    median_value: float | None


@dataclass
class EventProfile:
    name: str
    n_observations: int
    n_encounters: int
    coverage: float
    n_unique: int
    top_values: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class StaticColumnProfile:
    name: str
    dtype: str
    kind: str  # numeric | categorical | identifier | text | boolean | temporal | other
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
    sampled_encounters: int = 0


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
    events: list[EventProfile]
    static_columns: list[StaticColumnProfile]
    warnings: list[str]
    intervals: list[IntervalProfile] = field(default_factory=list)
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

    def events_frame(self) -> pl.DataFrame:
        rows = [
            {**asdict(e), "top_values": ", ".join(f"{v} ({n})" for v, n in e.top_values)}
            for e in self.events
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
        appear only where every shown category covers at least :data:`MIN_CATEGORY_COUNT`
        encounters.
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
            "events": [
                {
                    "name": e.name,
                    "n_observations": e.n_observations,
                    "coverage": r(e.coverage),
                    "n_unique": e.n_unique,
                    "categories": [v for v, _ in e.top_values] or None,
                }
                for e in self.events
            ],
            "intervals": [
                {
                    "name": i.name,
                    "n_episodes": i.n_episodes,
                    "coverage": r(i.coverage),
                    "median_hours": r(i.median_hours),
                    "median_value": r(i.median_value),
                }
                for i in self.intervals
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


def _sample_ids(cohort: Cohort) -> list[str]:
    ids = cohort.encounter_ids
    step = max(1, len(ids) // INTERVAL_SAMPLE)
    return ids[::step][:INTERVAL_SAMPLE]


def _sample_stats(cohort: Cohort) -> tuple[pl.DataFrame, pl.Series, int]:
    """Distribution, uniqueness and interval statistics from a sample of encounters.

    These need whole groups in memory (quantiles, distinct counts, sorted gaps), so they are
    computed on at most :data:`INTERVAL_SAMPLE` encounters; everything else streams.
    """
    ids = _sample_ids(cohort)
    sample = collect(
        cohort.sorted_dynamic()
        .filter(pl.col(ENCOUNTER_ID).is_in(ids))
        .with_columns(
            pl.col(TIME).diff().over([ENCOUNTER_ID, VARIABLE]).dt.total_seconds().alias("_gap")
        )
    )
    per_encounter = sample.group_by(ENCOUNTER_ID, VARIABLE).agg(
        pl.col(VALUE).n_unique().alias("_nu"), pl.len().alias("_n")
    )
    stats = (
        sample.group_by(VARIABLE)
        .agg(
            pl.col(VALUE).quantile(0.01).alias("p01"),
            pl.col(VALUE).median().alias("median"),
            pl.col(VALUE).quantile(0.99).alias("p99"),
            pl.col(VALUE).n_unique().alias("n_unique"),
            pl.len().alias("_n_sampled"),
            ((pl.col(VALUE) - pl.col(VALUE).round(0)).abs().max() < 1e-9).alias("_integer"),
            pl.col("_gap").median().alias("median_interval_s"),
        )
        .join(
            per_encounter.group_by(VARIABLE).agg(
                pl.col("_nu").max().alias("_max_unique"), pl.col("_n").max().alias("_max_obs")
            ),
            on=VARIABLE,
            how="left",
        )
    )
    return stats, sample.get_column("_gap").drop_nulls(), len(ids)


def _variable_profiles(
    cohort: Cohort, n_encounters: int
) -> tuple[list[VariableProfile], pl.Series, int]:
    exact = collect(
        cohort.dynamic.group_by(VARIABLE).agg(
            pl.len().alias("n_observations"),
            pl.col(ENCOUNTER_ID).n_unique().alias("n_encounters"),
            pl.col(VALUE).min().alias("min"),
            pl.col(VALUE).max().alias("max"),
            pl.col(VALUE).mean().alias("mean"),
            pl.col(VALUE).std().alias("std"),
        )
    )
    sampled_stats, gaps, sampled = _sample_stats(cohort)
    stats = exact.join(sampled_stats, on=VARIABLE, how="left")
    profiles = []
    for row in stats.sort(VARIABLE).iter_rows(named=True):
        flags: list[str] = []
        coverage = row["n_encounters"] / n_encounters if n_encounters else 0.0
        n_unique = row["n_unique"] or 0
        if n_unique <= 1:
            flags.append("constant")
        elif row["min"] >= 0 and row["max"] <= 1 and n_unique <= 2:
            flags.append("binary")
        if n_unique > 1 and row["_max_unique"] == 1 and (row["_max_obs"] or 0) > 1:
            flags.append("constant_per_encounter")
        elif (row["_max_obs"] or 0) == 1 and row["n_encounters"] > 1:
            flags.append("single_observation")
        if row["_integer"]:
            flags.append("integer")
        if coverage < 0.1:
            flags.append("low_coverage")
        if row["median_interval_s"] is not None and row["median_interval_s"] >= 86400:
            flags.append("sparse")
        if row["_integer"] and (row["_n_sampled"] or 0) > 10 and n_unique == row["_n_sampled"]:
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
                n_unique=int(n_unique),
                median_interval_s=row["median_interval_s"],
                flags=flags,
            )
        )
    return profiles, gaps, sampled


def _event_profiles(cohort: Cohort, n_encounters: int) -> list[EventProfile]:
    stats = collect(
        cohort.events.group_by(VARIABLE).agg(
            pl.len().alias("n_observations"),
            pl.col(ENCOUNTER_ID).n_unique().alias("n_encounters"),
            pl.col(VALUE).n_unique().alias("n_unique"),
        )
    ).sort(VARIABLE)
    tops = collect(
        cohort.events.group_by(VARIABLE, VALUE)
        .agg(pl.col(ENCOUNTER_ID).n_unique().alias("n"))
        .filter(pl.col("n") >= MIN_CATEGORY_COUNT)
    ).sort(["n", VALUE], descending=[True, False])
    out = []
    for row in stats.iter_rows(named=True):
        top = tops.filter(pl.col(VARIABLE) == row[VARIABLE]).head(MAX_CATEGORIES)
        out.append(
            EventProfile(
                name=row[VARIABLE],
                n_observations=int(row["n_observations"]),
                n_encounters=int(row["n_encounters"]),
                coverage=round(row["n_encounters"] / n_encounters, 4) if n_encounters else 0.0,
                n_unique=int(row["n_unique"]),
                top_values=[(str(v), int(n)) for v, n in top.select(VALUE, "n").iter_rows()],
            )
        )
    return out


def _sampling_profile(gaps: pl.Series, sampled: int) -> SamplingProfile:
    if gaps.is_empty():
        return SamplingProfile(None, None, None, None, None, sampled)
    median = float(gaps.median())
    mode_count = gaps.round(0).value_counts(sort=True).get_column("count")[0]
    return SamplingProfile(
        median_interval_s=median,
        p10_interval_s=float(gaps.quantile(0.1)),
        p90_interval_s=float(gaps.quantile(0.9)),
        regular_fraction=round(float(mode_count) / gaps.len(), 4),
        suggested_resample=snap_to_ladder(median),
        sampled_encounters=sampled,
    )


def _static_profiles(static: pl.DataFrame) -> list[StaticColumnProfile]:
    out = []
    n = static.height
    for name in static.columns:
        if name == ENCOUNTER_ID:
            continue
        s = static.get_column(name)
        dtype = s.dtype
        n_unique = int(s.drop_nulls().n_unique())
        prof = StaticColumnProfile(
            name=name,
            dtype=str(dtype),
            kind="other",
            n_missing=int(s.null_count()),
            n_unique=n_unique,
        )
        if dtype.is_numeric():
            prof.kind = (
                "identifier" if n_unique == n and n > 20 and dtype.is_integer() else "numeric"
            )
            prof.min, prof.max, prof.mean = _f(s.min()), _f(s.max()), _f(s.mean())
        elif dtype == pl.Boolean:
            prof.kind = "boolean"
            counts = (
                s.drop_nulls()
                .cast(pl.String())
                .value_counts(sort=True)
                .filter(pl.col("count") >= MIN_CATEGORY_COUNT)
            )
            prof.top_values = [(str(v), int(c)) for v, c in counts.iter_rows()]
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
            elif _looks_like_codes(s):
                prof.kind = "codes"  # ICD lists and the like: short tokens, no prose
            else:
                prof.kind = "text"
        elif dtype.is_temporal():
            prof.kind = "temporal"
        out.append(prof)
    return out


_CODES = re.compile(r"^[A-Za-z0-9.\-/]{1,12}(\|[A-Za-z0-9.\-/]{1,12})*$")


def _looks_like_codes(s: pl.Series) -> bool:
    sample = s.drop_nulls().cast(pl.String()).head(200)
    return sample.len() > 0 and bool(sample.str.contains(_CODES.pattern).all())


def _profile_intervals(cohort: Cohort) -> list[IntervalProfile]:
    from dea.core.model import END, START

    n = len(cohort)
    rows = collect(
        cohort.intervals.group_by(VARIABLE).agg(
            pl.len().alias("n"),
            pl.col(ENCOUNTER_ID).n_unique().alias("enc"),
            ((pl.col(END) - pl.col(START)).dt.total_seconds() / 3600.0).median().alias("h"),
            pl.col(VALUE).median().alias("v"),
        )
    ).sort(VARIABLE)
    return [
        IntervalProfile(
            name=r[VARIABLE],
            n_episodes=int(r["n"]),
            n_encounters=int(r["enc"]),
            coverage=r["enc"] / n if n else 0.0,
            median_hours=_f(r["h"]),
            median_value=_f(r["v"]),
        )
        for r in rows.iter_rows(named=True)
    ]


def _f(x: Any) -> float | None:
    return None if x is None else float(x)


def profile_cohort(cohort: Cohort) -> CohortProfile:
    """Compute a :class:`CohortProfile` for ``cohort``."""
    n_enc = len(cohort)
    summary = cohort.summary()
    variables, gaps, sampled = _variable_profiles(cohort, n_enc)
    events = _event_profiles(cohort, n_enc)
    sampling = _sampling_profile(gaps, sampled)
    statics = _static_profiles(cohort.static)

    warnings: list[str] = []
    empty = cohort.features().filter(pl.col("n_observations") == 0).height
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
    for e in events:
        if e.n_unique > 50 and e.n_unique > 0.5 * e.n_observations:
            warnings.append(f"event '{e.name}' looks like free text; check for identifying content")
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
        events=events,
        intervals=_profile_intervals(cohort),
        static_columns=statics,
        warnings=warnings,
    )

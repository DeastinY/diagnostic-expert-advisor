"""Ready-made per-encounter jobs. Use them as templates for your own."""

from __future__ import annotations

from typing import Any

import polars as pl

from dea.core.model import TIME, VALUE, VARIABLE, Encounter


def encounter_summary(encounter: Encounter) -> dict[str, Any]:
    """Stay length, observation counts and the mean of every variable."""
    means = encounter.dynamic.group_by(VARIABLE).agg(pl.col(VALUE).mean()).sort(VARIABLE)
    out: dict[str, Any] = {
        "los_hours": round(encounter.los_hours, 2),
        "n_observations": encounter.n_observations,
        "n_variables": len(encounter.variables),
    }
    for var, mean in means.iter_rows():
        out[f"{var}__mean"] = mean
    return out


def variable_coverage(encounter: Encounter) -> pl.DataFrame:
    """One row per variable: count, first and last observation time, median interval."""
    if encounter.dynamic.is_empty():
        return pl.DataFrame(
            schema={
                VARIABLE: pl.String(),
                "count": pl.UInt32(),
                "first_hours": pl.Float64(),
                "last_hours": pl.Float64(),
                "median_interval_min": pl.Float64(),
            }
        )
    return (
        encounter.dynamic.sort(TIME)
        .with_columns(pl.col(TIME).diff().over(VARIABLE).dt.total_seconds().alias("_gap"))
        .group_by(VARIABLE)
        .agg(
            pl.len().alias("count"),
            (pl.col(TIME).min().dt.total_seconds() / 3600).alias("first_hours"),
            (pl.col(TIME).max().dt.total_seconds() / 3600).alias("last_hours"),
            (pl.col("_gap").median() / 60).alias("median_interval_min"),
        )
        .sort(VARIABLE)
    )


BUILTIN_JOBS = {
    "encounter_summary": encounter_summary,
    "variable_coverage": variable_coverage,
}

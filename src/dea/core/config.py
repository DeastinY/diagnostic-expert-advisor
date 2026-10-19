"""Cohort configuration: filters, tags, default plots and processing settings.

Everything domain specific (which variables matter, what "severe ARDS" means) lives here
rather than in code, so the same software serves any cohort. Filters and tags are SQL
expressions evaluated against the encounter feature table (see :meth:`Cohort.features`),
e.g. ``"Horowitz__min" < 100 AND los_hours > 24``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

FillStrategy = Literal["none", "forward", "interpolate", "zero"]
Aggregate = Literal["mean", "median", "first", "last", "min", "max"]


class Rule(BaseModel):
    """A named boolean expression over the encounter feature table."""

    label: str
    where: str = Field(description="SQL expression over encounter features")
    description: str = ""

    @field_validator("where")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("rule expression must not be empty")
        return value


class ValueFix(BaseModel):
    """A recorded rewrite of observation values (unit repair, implausible values dropped)."""

    variable: str
    where: str = Field(description="SQL condition over `value` selecting the affected rows")
    expr: str | None = Field(
        default=None, description="SQL expression for the new value; None drops the rows"
    )
    encounter_where: str | None = Field(
        default=None, description="SQL condition over the static table limiting the encounters"
    )
    description: str = ""


class ProcessingConfig(BaseModel):
    """How to turn irregular long-format observations into a regular wide frame."""

    resample_every: str | None = Field(
        default=None, description="Grid step such as '15m' or '1h'; None keeps raw timestamps"
    )
    aggregate: Aggregate = "mean"
    fill: FillStrategy = "none"
    fill_overrides: dict[str, FillStrategy] = Field(default_factory=dict)
    drop_variables: list[str] = Field(default_factory=list)
    pre_admission_lead_hours: float = Field(
        default=24,
        ge=0,
        description="Run-up before the admission time that still belongs to the stay (labs "
        "drawn in the emergency department); records further back are flagged as foreign",
    )

    def fill_for(self, variable: str) -> FillStrategy:
        return self.fill_overrides.get(variable, self.fill)


class DashboardPanel(BaseModel):
    """One chart on the overview: what to draw, with which parameters, how wide."""

    id: str
    kind: str = Field(
        description="los, tags, coverage, static, crosstab, variable, trajectory, ..."
    )
    params: dict[str, Any] = Field(default_factory=dict)
    size: int = Field(default=2, ge=1, le=4, description="columns of the four-column grid")
    title: str | None = None


class CohortConfig(BaseModel):
    """Everything DEA needs to know about a cohort besides the data itself."""

    name: str
    description: str = ""
    filters: list[Rule] = Field(default_factory=list)
    tags: list[Rule] = Field(default_factory=list)
    plots: list[str] = Field(default_factory=list, description="Variables to chart by default")
    dashboard: list[DashboardPanel] = Field(
        default_factory=list, description="The overview's panels; empty means DEA's default"
    )
    variable_labels: dict[str, str] = Field(default_factory=dict)
    variable_units: dict[str, str] = Field(default_factory=dict)
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)
    variable_map: dict[str, str] = Field(
        default_factory=dict, description="Source variable name -> canonical name, as applied"
    )
    site_column: str | None = Field(
        default=None,
        description="Static column naming the hospital/site/export an encounter came from",
    )
    variable_codes: dict[str, str] = Field(
        default_factory=dict, description="Variable -> LOINC code, when known"
    )
    unmapped: list[str] = Field(
        default_factory=list,
        description="Variables that must not be matched to a dictionary concept (a different "
        "measurement under a known name)",
    )
    value_fixes: list[ValueFix] = Field(
        default_factory=list, description="Value rewrites applied to the stored observations"
    )
    feature_variables: list[str] | None = Field(
        default=None,
        description="Variables aggregated in the feature table; default: the 200 most observed",
    )
    parent: str | None = Field(
        default=None, description="Path of the cohort this one was derived from"
    )
    derived_by: str | None = Field(default=None, description="Rule that selected this sub-cohort")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    dea_version: str = "2"
    source: str = ""

    def renamed(self, mapping: dict[str, str]) -> CohortConfig:
        """Copy of the config with variables renamed everywhere they are referenced."""
        new = self.model_copy(deep=True)

        def ren(name: str) -> str:
            return mapping.get(name, name)

        def ren_text(text: str) -> str:
            for old, new_name in mapping.items():
                text = text.replace(f"{old}__", f"{new_name}__")
            return text

        new.plots = [ren(v) for v in self.plots]
        new.variable_labels = {ren(k): v for k, v in self.variable_labels.items()}
        new.variable_units = {ren(k): v for k, v in self.variable_units.items()}
        new.variable_codes = {ren(k): v for k, v in self.variable_codes.items()}
        new.unmapped = [ren(v) for v in self.unmapped]
        for fix in new.value_fixes:
            fix.variable = ren(fix.variable)
        new.processing.drop_variables = [ren(v) for v in self.processing.drop_variables]
        new.processing.fill_overrides = {
            ren(k): v for k, v in self.processing.fill_overrides.items()
        }
        if self.feature_variables:
            new.feature_variables = [ren(v) for v in self.feature_variables]
        for rule in new.filters + new.tags:
            rule.where = ren_text(rule.where)
        new.variable_map = {**self.variable_map, **mapping}
        return new

    def label(self, variable: str) -> str:
        return self.variable_labels.get(variable, variable)

    def to_yaml(self) -> str:
        data = self.model_dump(mode="json")
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)

    @classmethod
    def from_yaml(cls, text: str) -> CohortConfig:
        return cls.model_validate(yaml.safe_load(text) or {})

    def save(self, path: Path) -> None:
        Path(path).write_text(self.to_yaml(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> CohortConfig:
        return cls.from_yaml(Path(path).read_text(encoding="utf-8"))

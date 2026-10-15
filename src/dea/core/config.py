"""Cohort configuration: filters, tags, default plots and processing settings.

Everything domain specific (which variables matter, what "severe ARDS" means) lives here
rather than in code, so the same software serves any cohort. Filters and tags are SQL
expressions evaluated against the encounter feature table (see :meth:`Cohort.features`),
e.g. ``"Horowitz__min" < 100 AND los_hours > 24``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

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


class ProcessingConfig(BaseModel):
    """How to turn irregular long-format observations into a regular wide frame."""

    resample_every: str | None = Field(
        default=None, description="Grid step such as '15m' or '1h'; None keeps raw timestamps"
    )
    aggregate: Aggregate = "mean"
    fill: FillStrategy = "none"
    fill_overrides: dict[str, FillStrategy] = Field(default_factory=dict)
    drop_variables: list[str] = Field(default_factory=list)

    def fill_for(self, variable: str) -> FillStrategy:
        return self.fill_overrides.get(variable, self.fill)


class CohortConfig(BaseModel):
    """Everything DEA needs to know about a cohort besides the data itself."""

    name: str
    description: str = ""
    filters: list[Rule] = Field(default_factory=list)
    tags: list[Rule] = Field(default_factory=list)
    plots: list[str] = Field(default_factory=list, description="Variables to chart by default")
    variable_labels: dict[str, str] = Field(default_factory=dict)
    variable_units: dict[str, str] = Field(default_factory=dict)
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)
    feature_variables: list[str] | None = Field(
        default=None,
        description="Variables aggregated in the feature table; default: the 200 most observed",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    dea_version: str = "2"
    source: str = ""

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

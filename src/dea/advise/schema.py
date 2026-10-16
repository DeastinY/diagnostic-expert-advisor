"""Structured recommendations returned by the advisor.

Kept strict-schema friendly (no dicts, no defaults) so OpenAI-style structured outputs can
enforce it; :meth:`Recommendation.apply_to` turns it into a cohort config.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from dea.core.config import CohortConfig, ProcessingConfig, Rule

Role = Literal[
    "vital_sign",
    "laboratory",
    "blood_gas",
    "ventilation",
    "medication",
    "fluid_balance",
    "score",
    "demographic",
    "identifier",
    "device",
    "other",
]
Fill = Literal["none", "forward", "interpolate", "zero", "drop"]


class VariableAdvice(BaseModel):
    variable: str = Field(description="Exact variable name from the profile")
    label: str = Field(description="Short human-readable English label")
    canonical_name: str = Field(
        description="Standard machine name for this variable (e.g. 'heart_rate', 'pao2_fio2'); "
        "empty string if the source name is already a good canonical name"
    )
    role: Role
    unit_guess: str = Field(description="Likely unit, or empty string if unknown")
    imputation: Fill = Field(description="How to fill gaps after resampling")
    rationale: str = Field(description="One sentence")


class SuggestedRule(BaseModel):
    label: str
    where: str = Field(
        description='SQL over encounter features, e.g. "Horowitz__min" < 100 AND los_hours > 24'
    )
    description: str


class Recommendation(BaseModel):
    summary: str = Field(description="Two to four sentences a clinician can read")
    resample_every: str = Field(description="Grid step like '15m' or '1h'")
    aggregate: Literal["mean", "median", "first", "last", "min", "max"]
    default_fill: Literal["none", "forward", "interpolate", "zero"]
    variables: list[VariableAdvice]
    suggested_filters: list[SuggestedRule]
    plot_variables: list[str] = Field(description="Up to eight variables worth charting by default")
    storage_notes: list[str]
    warnings: list[str]

    def variable_map(self, known_variables: list[str] | None = None) -> dict[str, str]:
        """Source name -> canonical name for every variable the advisor wants renamed."""
        known = set(known_variables) if known_variables is not None else None
        return {
            v.variable: v.canonical_name
            for v in self.variables
            if v.canonical_name
            and v.canonical_name != v.variable
            and (known is None or v.variable in known)
        }

    def to_processing_config(self) -> ProcessingConfig:
        overrides = {}
        drops = []
        for v in self.variables:
            if v.imputation == "drop":
                drops.append(v.variable)
            elif v.imputation != self.default_fill:
                overrides[v.variable] = v.imputation
        return ProcessingConfig(
            resample_every=self.resample_every,
            aggregate=self.aggregate,
            fill=self.default_fill,
            fill_overrides=overrides,
            drop_variables=drops,
        )

    def apply_to(
        self, config: CohortConfig, known_variables: list[str] | None = None
    ) -> CohortConfig:
        """Return a copy of ``config`` with the recommendation merged in.

        Existing filters with the same label are kept; unknown variables are ignored.
        """
        known = set(known_variables) if known_variables is not None else None
        new = config.model_copy(deep=True)
        new.processing = self.to_processing_config()
        if known is not None:
            new.processing.drop_variables = [v for v in new.processing.drop_variables if v in known]
            new.processing.fill_overrides = {
                k: v for k, v in new.processing.fill_overrides.items() if k in known
            }
        for v in self.variables:
            if known is not None and v.variable not in known:
                continue
            if v.label:
                new.variable_labels[v.variable] = v.label
            if v.unit_guess:
                new.variable_units[v.variable] = v.unit_guess
        existing = {r.label for r in new.filters}
        for rule in self.suggested_filters:
            if rule.label not in existing:
                new.filters.append(
                    Rule(label=rule.label, where=rule.where, description=rule.description)
                )
        plots = [p for p in self.plot_variables if known is None or p in known]
        if plots:
            new.plots = plots[:8]
        return new


class MappingProposal(BaseModel):
    variable: str = Field(description="Exact source variable name")
    concept: str = Field(
        description="Canonical concept name from the allowed list, or empty string when none fits"
    )
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(description="One short sentence")


class MappingProposals(BaseModel):
    proposals: list[MappingProposal]

    def as_mapping(self, min_confidence: float = 0.6) -> dict[str, str]:
        return {
            p.variable: p.concept
            for p in self.proposals
            if p.concept and p.concept != p.variable and p.confidence >= min_confidence
        }

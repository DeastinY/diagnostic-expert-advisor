"""Turn a profile into concrete, applicable suggestions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import polars as pl

from dea.core.config import Rule
from dea.core.model import ENCOUNTER_ID, Cohort
from dea.profile import CohortProfile, VariableProfile, profile_cohort
from dea.suggest.units import UnitTable, load_units
from dea.suggest.vocab import Concept, load_dictionary

Kind = Literal["harmonize", "static", "drop", "processing", "tag", "filter", "plot", "quality"]

# canonical concept -> (tag label, feature aggregate, operator, threshold, description)
CLINICAL_TAGS: list[tuple[str, str, str, str, float, str]] = [
    ("pao2_fio2", "Severe ARDS", "min", "<", 100, "Horowitz index below 100 mmHg at any time"),
    ("pao2_fio2", "Moderate ARDS", "min", "<", 200, "Horowitz index below 200 mmHg at any time"),
    ("map", "Hypotension", "min", "<", 65, "Mean arterial pressure below 65 mmHg at any time"),
    ("lactate", "Hyperlactatemia", "max", ">", 2, "Lactate above 2 mmol/L at any time"),
    ("norepinephrine", "Vasopressors", "max", ">", 0, "Received norepinephrine"),
    ("spo2", "Desaturation", "min", "<", 90, "SpO2 below 90% at any time"),
    ("creatinine", "Raised creatinine", "max", ">", 1.5, "Creatinine above 1.5 mg/dL at any time"),
    ("temperature", "Fever", "max", ">", 38.3, "Temperature above 38.3 °C at any time"),
]
PLOT_ROLES = ("vital_sign", "blood_gas", "ventilation", "medication")
# mass or amount per volume (ug/mL, mg/dL, mmol/L, U/L): a level, never a dose


@dataclass
class Suggestion:
    id: str
    kind: Kind
    title: str
    detail: str
    action: dict[str, Any] | None = None
    confidence: float = 1.0
    variables: list[str] = field(default_factory=list)
    severity: str | None = None  # set for quality checks: info, warning, error
    concept: dict[str, Any] | None = None  # for renames: the canonical name with its codes

    @property
    def applicable(self) -> bool:
        return self.action is not None

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "applicable": self.applicable}


def _matches(variables: list[str]) -> dict[str, tuple[Concept, float]]:
    d = load_dictionary()
    out = {}
    for v in variables:
        m = d.match(v)
        if m:
            out[v] = m
    return out


def unit_fits(units: UnitTable, unit: str | None, concept: Concept) -> bool:
    """Whether a variable recorded in ``unit`` can be the drug ``concept`` at all.

    A medication concept stands for a dose or an infusion rate in one unit. A variable whose
    recorded unit is that unit, or converts into it (mcg/kg/min into mg/kg/h), fits. One whose
    unit cannot be converted at all does not, however well the name matches: a concentration
    under a drug's name is the drug *level* (vancomycin in ug/mL), "dose" under heparin is the
    subcutaneous prophylaxis, not the infusion. Variables without a recorded unit, and concepts
    that are not medications, are left to the unit normalizer and the range checks.
    """
    if not unit or concept.role != "medication" or not concept.ucum:
        return True
    ucum = units.to_ucum(unit) or ""
    return ucum == concept.ucum or units.conversion(ucum, concept.ucum, concept.name) is not None


def _fill_for(concept: Concept | None, prof: VariableProfile) -> str:
    if concept:
        return concept.fill
    gap = prof.median_interval_s or 0
    if gap and gap <= 300:
        return "interpolate"
    return "forward"


def suggest(cohort: Cohort, profile: CohortProfile | None = None) -> list[Suggestion]:
    """Rule-based suggestions for a cohort. Pure function of the cohort and its profile."""
    profile = profile or profile_cohort(cohort)
    cfg = cohort.config
    out: list[Suggestion] = []
    by_name = {v.name: v for v in profile.variables}
    interval_vars = cohort.interval_variables
    matches = _matches(list(by_name) + interval_vars)
    for v in cohort.config.unmapped:
        matches.pop(v, None)
    units = load_units()
    for v, (concept, _) in list(matches.items()):  # a level in ug/mL is not the dose in mg
        if not unit_fits(units, cfg.variable_units.get(v), concept):
            matches.pop(v)
    canonical_present = {c.name for c, _ in matches.values()}

    # 1. harmonization: local names become the canonical name, which carries the codes
    by_concept: dict[str, list[str]] = {}
    for var, (concept, _) in sorted(matches.items()):
        by_concept.setdefault(concept.name, []).append(var)
    for var, (concept, score) in sorted(matches.items()):
        if var == concept.name:
            continue
        joins = [v for v in by_concept[concept.name] if v != var]
        recorded = cfg.variable_units.get(var)
        detail = f"Recognized as {concept.label}" + (f" ({concept.unit})" if concept.unit else "")
        detail += f"; match confidence {score:.0%}."
        if recorded and concept.unit and recorded != concept.unit:
            ucum = units.to_ucum(recorded) or recorded
            if ucum == concept.ucum:
                pass
            elif units.conversion(ucum, concept.ucum, concept.name) is not None:
                detail += f" Recorded in {recorded}, converted to {concept.unit}."
            else:
                detail += f" Recorded in {recorded}, which does not convert to {concept.unit}."
        if joins:
            detail += " Becomes one variable with " + ", ".join(f"'{v}'" for v in joins) + "."
        out.append(
            Suggestion(
                id=f"rename:{var}",
                kind="harmonize",
                title=f"Rename '{var}' to '{concept.name}'",
                detail=detail,
                action={
                    "rename": {var: concept.name},
                    "label": {concept.name: concept.label},
                    "unit": {concept.name: concept.unit},
                },
                confidence=round(score, 2),
                variables=[var],
                concept={
                    "name": concept.name,
                    "label": concept.label,
                    "unit": concept.unit,
                    "loinc": concept.loinc,
                    "ucum": concept.ucum,
                    "rxnorm": concept.rxnorm,
                    "atc": concept.atc,
                    "joins": joins,
                },
            )
        )

    # 2. data quality checks (units, plausible ranges, clusters, duplicates, time)
    from dea.quality.checks import run_checks  # local import: checks use the dictionary

    for c in run_checks(cohort, profile):
        out.append(
            Suggestion(
                id=f"check:{c.id}",
                kind="quality",
                title=c.title,
                detail=c.detail,
                action=c.action,
                confidence={"error": 0.9, "warning": 0.7, "info": 0.5}[c.severity],
                variables=c.variables,
                severity=c.severity,
            )
        )

    # 3. static attributes recorded as time series; identifiers; constants
    for var, prof in by_name.items():
        concept = matches.get(var, (None, 0))[0]
        static_like = "constant_per_encounter" in prof.flags or (
            "single_observation" in prof.flags
            and concept is not None
            and concept.role == "demographic"
        )
        if static_like:
            out.append(
                Suggestion(
                    id=f"static:{var}",
                    kind="static",
                    title=f"Move '{var}' to the static table",
                    detail="Recorded once or never changes within an encounter; as a static column "
                    "it can be used in filters directly.",
                    action={"move_to_static": [var]},
                    variables=[var],
                )
            )
        elif (
            "suspected_identifier" in prof.flags or "constant" in prof.flags
        ) and var not in cfg.processing.drop_variables:
            why = (
                "looks like an identifier"
                if "suspected_identifier" in prof.flags
                else "is constant"
            )
            out.append(
                Suggestion(
                    id=f"drop:{var}",
                    kind="drop",
                    title=f"Exclude '{var}' from processing",
                    detail=f"It {why} and carries no clinical signal.",
                    action={"drop": [var]},
                    variables=[var],
                )
            )

    # 4. processing defaults
    grid = profile.sampling.suggested_resample
    overrides = {}
    for var, prof in by_name.items():
        concept = matches.get(var, (None, 0))[0]
        fill = _fill_for(concept, prof)
        if fill != "forward":
            overrides[var] = fill
    proc_action: dict[str, Any] = {"processing": {"fill": "forward", "fill_overrides": overrides}}
    if grid and grid != cfg.processing.resample_every:
        proc_action["processing"]["resample_every"] = grid
    changes = (
        (grid and grid != cfg.processing.resample_every)
        or overrides != cfg.processing.fill_overrides
        or cfg.processing.fill != "forward"
    )
    if changes:
        n_int = sum(1 for v in overrides.values() if v == "interpolate")
        n_zero = sum(1 for v in overrides.values() if v == "zero")
        out.append(
            Suggestion(
                id="processing",
                kind="processing",
                title=(
                    f"Resample to a {grid or cfg.processing.resample_every or 'raw'} grid, "
                    f"forward-fill labs, interpolate {n_int} continuous signal(s)"
                    + (f", zero-fill {n_zero} infusion(s)" if n_zero else "")
                ),
                detail=(
                    f"Typical sampling interval is {profile.sampling.median_interval_s or 0:.0f} s "
                    f"({profile.sampling.regular_fraction or 0:.0%} regular). "
                    "Fill strategies follow the variable role."
                ),
                action=proc_action,
            )
        )

    # 5. tags from standard thresholds (use current variable names)
    existing = {r.label for r in cfg.tags} | {r.label for r in cfg.filters}
    concept_to_var = {c.name: v for v, (c, _) in matches.items()}
    for var in interval_vars:  # interventions: "received X" tags from their duration
        concept = matches.get(var, (None, 0))[0]
        label = f"Received {(concept.name if concept else var).replace('_', ' ').lower()}"
        if label in existing:
            continue
        out.append(
            Suggestion(
                id=f"tag:{label}",
                kind="tag",
                title=f"Tag '{label}'",
                detail=f"Any episode of {var} during the stay.",
                action={
                    "tag": {
                        "label": label,
                        "where": f'"{var}__hours" > 0',
                        "description": f"At least one {var} episode",
                    }
                },
                variables=[var],
            )
        )
        existing.add(label)
    for concept_name, label, agg, op, threshold, desc in CLINICAL_TAGS:
        var = concept_to_var.get(concept_name)
        if not var or label in existing or var in interval_vars:
            continue
        out.append(
            Suggestion(
                id=f"tag:{label}",
                kind="tag",
                title=f"Tag '{label}'",
                detail=desc,
                action={
                    "tag": {
                        "label": label,
                        "where": f'"{var}__{agg}" {op} {threshold}',
                        "description": desc,
                    }
                },
                variables=[var],
            )
        )
    if "Short stay" not in existing:
        out.append(
            Suggestion(
                id="tag:Short stay",
                kind="tag",
                title="Tag 'Short stay'",
                detail="Less than three days",
                action={
                    "tag": {
                        "label": "Short stay",
                        "where": "los_hours < 72",
                        "description": "Stay under 72 hours",
                    }
                },
            )
        )
    for s in profile.static_columns:
        if s.kind == "boolean":
            values = [(v, f"{s.name} = true") for v, _ in s.top_values if v == "true"]
        elif s.kind == "categorical" and s.top_values and len(s.top_values) <= 6:
            values = [(v, f"{s.name} = '{v}'") for v, _ in s.top_values]
        else:
            continue
        for value, where in values:
            label = f"{s.name} = {value}"
            if label in existing:
                continue
            out.append(
                Suggestion(
                    id=f"filter:{label}",
                    kind="filter",
                    title=f"Filter '{label}'",
                    detail=f"Encounters with {s.name} equal to {value}.",
                    action={"filter": {"label": label, "where": where, "description": ""}},
                )
            )

    # 6. default plots
    if not cfg.plots:
        observed = {v: m for v, m in matches.items() if v in by_name}  # not intervals
        plots = [
            v
            for v, (c, _) in sorted(observed.items(), key=lambda kv: -by_name[kv[0]].n_observations)
            if c.role in PLOT_ROLES
        ][:8]
        if plots:
            out.append(
                Suggestion(
                    id="plots",
                    kind="plot",
                    title="Chart the main signals by default",
                    detail=", ".join(plots),
                    action={"plots": plots},
                    variables=plots,
                )
            )

    _ = canonical_present
    return out


def apply_suggestions(cohort: Cohort, suggestions: list[Suggestion]) -> Cohort:
    """Apply the actions of the given suggestions; returns a new cohort (not yet saved)."""
    renames: dict[str, str] = {}
    labels: dict[str, str] = {}
    units: dict[str, str] = {}
    to_static: list[str] = []
    drops: list[str] = []
    rules_tag: list[Rule] = []
    rules_filter: list[Rule] = []
    plots: list[str] | None = None
    processing: dict[str, Any] | None = None
    fixes: list[dict[str, Any]] = []
    parse: list[str] = []
    unmap: list[str] = []
    dedupes: list[str] = []
    drop_negative = drop_early = clamp_run_up = False
    for s in suggestions:
        a = s.action or {}
        fixes += [a["fix"]] if "fix" in a else []
        fixes += a.get("fixes", [])
        unmap += [a["unmap"]] if "unmap" in a else []
        if a.get("dedupe"):  # True is the old spelling of "first"
            how = "first" if a["dedupe"] is True else str(a["dedupe"])
            if how not in dedupes:
                dedupes.append(how)
        drop_negative = drop_negative or a.get("drop_time") == "negative"
        drop_early = drop_early or a.get("drop_time") == "early"
        clamp_run_up = clamp_run_up or a.get("drop_time") == "clamp"
        parse += [a["parse"]["variable"]] if "parse" in a else []
        renames.update(a.get("rename", {}))
        labels.update(a.get("label", {}))
        units.update({k: v for k, v in a.get("unit", {}).items() if v})
        to_static += a.get("move_to_static", [])
        drops += a.get("drop", [])
        if "tag" in a:
            rules_tag.append(Rule(**a["tag"]))
        if "filter" in a:
            rules_filter.append(Rule(**a["filter"]))
        if "plots" in a:
            plots = a["plots"]
        if "processing" in a:
            processing = a["processing"]
    out = cohort
    for f in fixes:
        out = out.fix_values(
            f["variable"],
            f["where"],
            f.get("expr"),
            f.get("description", ""),
            encounter_where=f.get("encounter_where"),
        )
    for how in sorted(dedupes, key=lambda h: h != "exact"):  # exact repeats go first
        out = out.dedupe(how)
    lead = cohort.config.processing.pre_admission_lead_hours
    if drop_negative:
        out = out.drop_before_admission()
    elif drop_early:
        out = out.drop_before_admission(lead_hours=lead)
    if clamp_run_up and not drop_negative:
        out = out.clamp_to_admission(lead_hours=lead)
    for v in parse:
        out = out.parse_numeric_events(v)
    out = out.move_to_static([v for v in to_static if v in cohort.variables])
    out = out.rename_variables(renames)
    cfg = out.config
    for v in unmap:  # not the concept it looked like: forget code and label, remember that
        cfg.variable_codes.pop(v, None)
        cfg.variable_labels.pop(v, None)
        if v not in cfg.unmapped:
            cfg.unmapped.append(v)
    cfg.variable_labels.update(labels)
    cfg.variable_units.update(units)
    ren = lambda v: renames.get(v, v)  # noqa: E731
    for v in drops:
        if ren(v) not in cfg.processing.drop_variables:
            cfg.processing.drop_variables.append(ren(v))
    if processing:
        if "resample_every" in processing:
            cfg.processing.resample_every = processing["resample_every"]
        if "fill" in processing:
            cfg.processing.fill = processing["fill"]
        if "fill_overrides" in processing:
            cfg.processing.fill_overrides = {
                ren(k): v for k, v in processing["fill_overrides"].items()
            }
    existing_tags = {r.label for r in cfg.tags}
    existing_filters = {r.label for r in cfg.filters}
    for r in rules_tag:
        if r.label not in existing_tags:
            cfg.tags.append(_renamed_rule(r, renames))
    for r in rules_filter:
        if r.label not in existing_filters:
            cfg.filters.append(_renamed_rule(r, renames))
    if plots is not None:
        cfg.plots = [ren(v) for v in plots]
    return out


def _renamed_rule(rule: Rule, renames: dict[str, str]) -> Rule:
    where = rule.where
    for old, new in renames.items():
        where = where.replace(f"{old}__", f"{new}__")
    return Rule(label=rule.label, where=where, description=rule.description)


def summarize(cohort: Cohort, suggestions: list[Suggestion]) -> pl.DataFrame:
    rows = [
        {"kind": s.kind, "title": s.title, "applicable": s.applicable, "confidence": s.confidence}
        for s in suggestions
    ]
    return (
        pl.DataFrame(rows)
        if rows
        else pl.DataFrame(schema={"kind": pl.String(), "title": pl.String()})
    )


__all__ = ["Suggestion", "suggest", "apply_suggestions", "summarize", "unit_fits", "ENCOUNTER_ID"]

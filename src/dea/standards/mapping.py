"""Site mapping files: local variable names or item codes -> dictionary concepts.

A hospital's export names things its own way (``"ABP mean"``, ``"K+ (venous)"``, item ``220045``).
Instead of renaming by hand after every import, a site keeps one YAML file::

    heart_rate: ["HR", "Herzfrequenz", "220045"]
    map: ["ABP mean", "ABPm"]
    "Temperature Celsius": temperature      # plain  source: target  pairs work too
    units:
      "ABP mean": mmHg                     # optional display units for targets

Targets that are dictionary concepts bring their label, unit and LOINC code along; other
targets are plain renames. ``dea import --mapping site.yaml`` applies it during import and
``dea rename --mapping site.yaml`` to an existing cohort.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from dea.core.model import Cohort
from dea.suggest.vocab import load_dictionary


def load_mapping(path: Path | str) -> dict[str, str]:
    """Source name -> target name, from the YAML forms shown in the module docstring."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if key == "units":
            continue
        if isinstance(value, list):
            for source in value:
                out[str(source)] = str(key)
        elif value is None:
            continue
        else:
            out[str(key)] = str(value)
    return out


def load_units(path: Path | str) -> dict[str, str]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {str(k): str(v) for k, v in (raw.get("units") or {}).items()}


def apply_mapping(
    cohort: Cohort, mapping: dict[str, str], units: dict[str, str] | None = None
) -> Cohort:
    """Rename variables (observations, events and intervals) and annotate concept targets."""
    present = set(cohort.variables) | set(cohort.event_variables) | set(cohort.interval_variables)
    applicable = {k: v for k, v in mapping.items() if k in present and k != v}
    out = cohort.rename_variables(applicable)
    d = load_dictionary()
    cfg = out.config
    for target in set(applicable.values()):
        concept = d.by_name.get(target)
        if concept is None:
            continue
        cfg.variable_labels.setdefault(target, concept.label)
        if concept.unit:
            cfg.variable_units.setdefault(target, concept.unit)
        if concept.loinc:
            cfg.variable_codes.setdefault(target, concept.loinc)
    for var, unit in (units or {}).items():
        cfg.variable_units[applicable.get(var, var)] = unit
    return out


def mapping_report(cohort: Cohort, mapping: dict[str, str]) -> dict[str, Any]:
    present = set(cohort.variables) | set(cohort.event_variables) | set(cohort.interval_variables)
    return {
        "applied": sorted(k for k in mapping if k in present),
        "unused": sorted(k for k in mapping if k not in present),
        "unmapped": sorted(v for v in present if v not in mapping),
    }


__all__ = ["apply_mapping", "load_mapping", "load_units", "mapping_report"]

"""Synthetic ICU-like cohorts for demos and tests. No real patients involved."""

from dea.synth.generate import (
    default_config,
    generate_cohort,
    write_legacy_csv_tree,
    write_synthetic,
)

__all__ = ["default_config", "generate_cohort", "write_legacy_csv_tree", "write_synthetic"]

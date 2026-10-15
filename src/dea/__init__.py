"""Diagnostic Expert Advisor (DEA).

A small platform for clinical time-series cohorts: ingest heterogeneous data into one
structure, profile it, visualize it, and hand researchers a Polars or pandas frame for
machine learning, with an optional AI advisor and an HPC compute backend.

The public API lives in :mod:`dea.core` (data model), :mod:`dea.io` (import, storage),
:mod:`dea.profile` (deterministic profiling), :mod:`dea.compute` (backends),
:mod:`dea.advise` (optional AI recommendations) and :mod:`dea.synth` (synthetic data).
"""

from dea.core import Cohort, CohortConfig, Encounter, ProcessingConfig, Rule
from dea.io import import_path, list_cohorts, load_cohort, save_cohort

__version__ = "2.0.0a1"

__all__ = [
    "Cohort",
    "CohortConfig",
    "Encounter",
    "ProcessingConfig",
    "Rule",
    "__version__",
    "import_path",
    "list_cohorts",
    "load_cohort",
    "save_cohort",
]

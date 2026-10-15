"""Compute backends: run a function over every encounter locally or on a cluster."""

from dea.compute.backend import (
    Backend,
    LocalBackend,
    SerialBackend,
    SlurmBackend,
    collect_results,
    get_backend,
)
from dea.compute.jobs import BUILTIN_JOBS, encounter_summary, variable_coverage

__all__ = [
    "BUILTIN_JOBS",
    "Backend",
    "LocalBackend",
    "SerialBackend",
    "SlurmBackend",
    "collect_results",
    "encounter_summary",
    "get_backend",
    "variable_coverage",
]

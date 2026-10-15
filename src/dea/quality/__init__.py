"""Data quality checks that run at import time and on demand."""

from dea.quality.checks import Check, run_checks

__all__ = ["Check", "run_checks"]

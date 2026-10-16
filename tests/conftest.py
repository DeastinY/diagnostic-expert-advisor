from __future__ import annotations

import os
from pathlib import Path

import pytest

from dea.core import Cohort
from dea.synth import generate_cohort

os.environ.setdefault("DEA_NO_UPDATE_CHECK", "1")  # no GitHub calls from the test suite


@pytest.fixture(scope="session")
def cohort() -> Cohort:
    """A small in-memory synthetic cohort shared across tests."""
    return generate_cohort(n_encounters=6, seed=42, name="unit", max_hours=72)


@pytest.fixture(scope="session")
def cohort_dir(tmp_path_factory: pytest.TempPathFactory, cohort: Cohort) -> Path:
    """The same cohort saved to disk."""
    path = tmp_path_factory.mktemp("data") / "unit"
    cohort.save(path)
    return path

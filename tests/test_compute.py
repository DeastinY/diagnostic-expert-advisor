from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from dea.compute import (
    BUILTIN_JOBS,
    LocalBackend,
    SerialBackend,
    SlurmBackend,
    collect_results,
    encounter_summary,
    get_backend,
    variable_coverage,
)
from dea.core import ENCOUNTER_ID, Cohort, Encounter
from dea.io import load_cohort


def test_builtin_jobs_serial(cohort: Cohort) -> None:
    out = cohort.apply(encounter_summary, SerialBackend())
    assert out.height == len(cohort)
    assert {"encounter_id", "los_hours", "n_observations", "HR__mean"} <= set(out.columns)
    cov = cohort.apply(variable_coverage)
    assert set(cov["variable"].unique()) == set(cohort.variables)
    assert cov.columns[0] == ENCOUNTER_ID
    assert set(BUILTIN_JOBS) == {"encounter_summary", "variable_coverage"}


@pytest.mark.parametrize("chunk_size", [1, 4])
def test_local_backend_matches_serial(cohort: Cohort, chunk_size: int) -> None:
    serial = cohort.apply(encounter_summary, SerialBackend()).sort(ENCOUNTER_ID)
    backend = LocalBackend(max_workers=2, chunk_size=chunk_size)
    local = cohort.apply(encounter_summary, backend).sort(ENCOUNTER_ID)
    # means are reduced in a different chunk order per process; allow last-digit noise
    assert_frame_equal(serial, local, check_exact=False, rtol=1e-9)


def test_apply_stores_derived(cohort_dir: Path) -> None:
    c = load_cohort(cohort_dir)
    out = c.apply(encounter_summary, name="summary")
    assert c.list_derived() == ["summary"]
    assert c.derived("summary").equals(out)
    assert c.derived("missing") is None


def test_apply_subset_and_none_results(cohort: Cohort) -> None:
    ids = cohort.encounter_ids[:2]

    def maybe(e: Encounter) -> dict | None:
        return {"n": e.n_observations} if e.id == ids[0] else None

    out = cohort.apply(maybe, ids=ids)
    assert out[ENCOUNTER_ID].to_list() == [ids[0]]


def test_collect_results_validation() -> None:
    assert collect_results([], []).columns == [ENCOUNTER_ID]
    with pytest.raises(TypeError):
        collect_results(["a"], [42])
    with pytest.raises(TypeError):
        collect_results(["a", "b"], [{"x": 1}, pl.DataFrame({"y": [1]})])
    mixed = collect_results(["a", "b"], [pl.DataFrame({"y": [1]}), pl.DataFrame({"z": [2.0]})])
    assert set(mixed.columns) == {ENCOUNTER_ID, "y", "z"}


def test_get_backend() -> None:
    assert isinstance(get_backend("serial"), SerialBackend)
    assert isinstance(get_backend("local", max_workers=1), LocalBackend)
    slurm = get_backend("slurm", folder="x", partition="gpu", account="acc", mem_gb=8)
    assert isinstance(slurm, SlurmBackend) and slurm.chunk_size == 200
    assert slurm.params["slurm_partition"] == "gpu" and slurm.params["mem_gb"] == 8
    with pytest.raises(ValueError):
        get_backend("cloud")

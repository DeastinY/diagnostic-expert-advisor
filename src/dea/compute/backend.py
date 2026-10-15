"""Backends share one contract: ``map(fn, items) -> list`` with results in input order.

``fn`` receives an :class:`~dea.core.Encounter` and returns a dict (one row), a Polars
DataFrame (many rows) or ``None``. Functions must be importable module-level callables for
the process and SLURM backends, because they are pickled.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import polars as pl

from dea.core.model import ENCOUNTER_ID

Result = dict[str, Any] | pl.DataFrame | None


@runtime_checkable
class Backend(Protocol):
    name: str

    def map(self, fn: Callable[[Any], Result], items: Sequence[Any]) -> list[Result]: ...


class SerialBackend:
    """Run in the current process. Always available, useful for debugging."""

    name = "serial"

    def map(self, fn: Callable[[Any], Result], items: Sequence[Any]) -> list[Result]:
        return [fn(item) for item in items]


class LocalBackend:
    """Run across local CPU cores with a process pool."""

    name = "local"

    def __init__(self, max_workers: int | None = None) -> None:
        self.max_workers = max_workers or max(1, (os.cpu_count() or 2) - 1)

    def map(self, fn: Callable[[Any], Result], items: Sequence[Any]) -> list[Result]:
        if not items:
            return []
        with ProcessPoolExecutor(
            max_workers=min(self.max_workers, len(items)), mp_context=get_context("spawn")
        ) as pool:
            return list(pool.map(fn, items, chunksize=1))


class SlurmBackend:
    """Submit one SLURM array job through `submitit <https://github.com/facebookincubator/submitit>`_.

    Requires ``dea[slurm]`` and a machine that can run ``sbatch``. Each encounter becomes one
    array task; results are collected by waiting on the jobs.

    .. note:: This adapter has not yet been exercised against a real cluster. Treat it as
       the intended shape of the integration rather than a tested feature.
    """

    name = "slurm"

    def __init__(
        self,
        folder: Path | str = "logs/dea",
        timeout_min: int = 60,
        mem_gb: int = 4,
        cpus_per_task: int = 1,
        partition: str | None = None,
        account: str | None = None,
        array_parallelism: int = 64,
        **slurm_kwargs: Any,
    ) -> None:
        self.folder = Path(folder)
        self.params: dict[str, Any] = {
            "timeout_min": timeout_min,
            "mem_gb": mem_gb,
            "cpus_per_task": cpus_per_task,
            "slurm_array_parallelism": array_parallelism,
        }
        if partition:
            self.params["slurm_partition"] = partition
        if account:
            self.params["slurm_account"] = account
        self.params.update(slurm_kwargs)

    def map(self, fn: Callable[[Any], Result], items: Sequence[Any]) -> list[Result]:
        try:
            import submitit
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "SlurmBackend needs the 'submitit' package: pip install dea[slurm]"
            ) from exc
        if not items:
            return []
        executor = submitit.AutoExecutor(folder=str(self.folder))
        executor.update_parameters(**self.params)
        jobs = executor.map_array(fn, list(items))
        return [job.result() for job in jobs]


_BACKENDS: dict[str, type] = {
    SerialBackend.name: SerialBackend,
    LocalBackend.name: LocalBackend,
    SlurmBackend.name: SlurmBackend,
}


def get_backend(name: str, **kwargs: Any) -> Backend:
    try:
        return _BACKENDS[name](**kwargs)
    except KeyError:
        raise ValueError(f"unknown backend {name!r}; choose from {sorted(_BACKENDS)}") from None


def collect_results(ids: Sequence[str], results: Sequence[Result]) -> pl.DataFrame:
    """Turn per-encounter results into one DataFrame with an ``encounter_id`` column."""
    rows: list[dict[str, Any]] = []
    frames: list[pl.DataFrame] = []
    for eid, res in zip(ids, results, strict=True):
        if res is None:
            continue
        if isinstance(res, pl.DataFrame):
            frames.append(res.with_columns(pl.lit(eid).alias(ENCOUNTER_ID)))
        elif isinstance(res, dict):
            rows.append({ENCOUNTER_ID: eid, **res})
        else:
            raise TypeError(f"job returned {type(res).__name__}; expected dict, DataFrame or None")
    if rows and frames:
        raise TypeError("job returned a mix of dicts and DataFrames")
    if frames:
        out = pl.concat(frames, how="diagonal_relaxed")
        return out.select(ENCOUNTER_ID, *[c for c in out.columns if c != ENCOUNTER_ID])
    if rows:
        return pl.DataFrame(rows, infer_schema_length=None)
    return pl.DataFrame({ENCOUNTER_ID: []}, schema={ENCOUNTER_ID: pl.String()})

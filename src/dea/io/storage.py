"""On-disk layout of a DEA cohort.

::

    <cohort>/
      cohort.yaml                 configuration (filters, tags, plots, processing)
      static.parquet              one row per encounter, keyed by encounter_id
      dynamic/                    long-format observations, hive-partitioned
        encounter_id=<id>/*.parquet
      derived/                    results of Cohort.apply(..., name=...)
        <name>.parquet

Parquet keeps types intact (no index columns creeping into CSVs), compresses well, and
lets Polars scan only the encounters a query touches.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from dea.core.config import CohortConfig
from dea.core.model import ENCOUNTER_ID, LONG_SCHEMA, Cohort

COHORT_FILE = "cohort.yaml"
STATIC_FILE = "static.parquet"
DYNAMIC_DIR = "dynamic"
DERIVED_DIR = "derived"
_BATCH = 64


def is_cohort_dir(path: Path | str) -> bool:
    p = Path(path)
    return (p / COHORT_FILE).is_file() and (p / STATIC_FILE).is_file()


def _scan_dynamic(dyn_dir: Path) -> pl.LazyFrame:
    if not dyn_dir.exists() or not any(dyn_dir.rglob("*.parquet")):
        return pl.LazyFrame(schema=LONG_SCHEMA)
    return pl.scan_parquet(
        dyn_dir / "**" / "*.parquet",
        hive_partitioning=True,
        hive_schema={ENCOUNTER_ID: pl.String()},
    ).select(list(LONG_SCHEMA))


def save_cohort(cohort: Cohort, path: Path | str) -> Path:
    """Write a cohort to ``path``; safe to call on the directory it was loaded from."""
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    cohort.config.save(root / COHORT_FILE)

    # Dynamic data first, into a temporary directory, so that saving in place never reads
    # from files that are being replaced.
    tmp = root / f"{DYNAMIC_DIR}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    ids = cohort.encounter_ids
    for start in range(0, len(ids), _BATCH):
        batch = ids[start : start + _BATCH]
        frame = cohort.dynamic.filter(pl.col(ENCOUNTER_ID).is_in(batch)).collect()
        if frame.is_empty():
            continue
        frame.write_parquet(tmp, partition_by=ENCOUNTER_ID)
    final = root / DYNAMIC_DIR
    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)

    cohort.static.write_parquet(root / STATIC_FILE)
    cohort.dynamic = _scan_dynamic(final)
    return root


def load_cohort(path: Path | str) -> Cohort:
    root = Path(path)
    if not is_cohort_dir(root):
        raise FileNotFoundError(f"{root} is not a DEA cohort (missing {COHORT_FILE}/{STATIC_FILE})")
    config = CohortConfig.load(root / COHORT_FILE)
    static = pl.read_parquet(root / STATIC_FILE)
    dynamic = _scan_dynamic(root / DYNAMIC_DIR)
    return Cohort(static, dynamic, config=config, root=root)


@dataclass(frozen=True)
class CohortInfo:
    name: str
    path: Path
    n_encounters: int
    description: str = ""


def list_cohorts(data_dir: Path | str) -> list[CohortInfo]:
    """Cohorts directly below ``data_dir``, sorted by name."""
    base = Path(data_dir)
    if not base.exists():
        return []
    infos = []
    for child in sorted(p for p in base.iterdir() if p.is_dir()):
        if not is_cohort_dir(child):
            continue
        try:
            config = CohortConfig.load(child / COHORT_FILE)
            n = pl.scan_parquet(child / STATIC_FILE).select(pl.len()).collect().item()
        except Exception:  # unreadable cohort dirs are skipped rather than crashing a listing
            continue
        infos.append(CohortInfo(config.name, child, int(n), config.description))
    return infos

"""On-disk layout of a DEA cohort.

::

    <cohort>/
      cohort.yaml                 configuration (filters, tags, plots, processing)
      static.parquet              one row per encounter, keyed by encounter_id
      dynamic/bucket=NN/*.parquet numeric observations, long format
      events/bucket=NN/*.parquet  non-numeric observations (strings), long format
      intervals/bucket=NN/*.parquet interventions with a start and an end (infusions, ventilation)
      derived/<name>.parquet      results of Cohort.apply(..., name=...) and caches

Observations are spread over a bounded number of hash buckets (at most 256) rather than
one directory per encounter, so a MIMIC-sized cohort is a few hundred files instead of
tens of thousands. Files are sorted by encounter and time, which lets Polars skip whole
row groups when a single encounter is requested. Everything is written with streaming
sinks, so writing never requires the data to fit in memory.
"""

from __future__ import annotations

import math
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from dea.core.config import CohortConfig
from dea.core.model import (
    ENCOUNTER_ID,
    EVENT_SCHEMA,
    INTERVAL_SCHEMA,
    LONG_SCHEMA,
    START,
    TIME,
    Cohort,
)

COHORT_FILE = "cohort.yaml"
STATIC_FILE = "static.parquet"
DYNAMIC_DIR = "dynamic"
EVENTS_DIR = "events"
INTERVALS_DIR = "intervals"
DERIVED_DIR = "derived"
BUCKET = "bucket"
ENCOUNTERS_PER_BUCKET = 500
MAX_BUCKETS = 256


def n_buckets(n_encounters: int) -> int:
    return max(1, min(MAX_BUCKETS, math.ceil(n_encounters / ENCOUNTERS_PER_BUCKET)))


def is_cohort_dir(path: Path | str) -> bool:
    p = Path(path)
    return (p / COHORT_FILE).is_file() and (p / STATIC_FILE).is_file()


def sink_long(
    frame: pl.LazyFrame | pl.DataFrame,
    directory: Path,
    buckets: int,
    tag: str = "data",
    time_col: str = TIME,
) -> None:
    """Stream a long frame into ``directory/bucket=NN/<tag>-<i>.parquet``, sorted per file.

    Several calls with different ``tag`` values can add to the same directory, which is how
    large cohorts are written in batches.
    """
    directory.mkdir(parents=True, exist_ok=True)

    def file_name(args) -> str:  # noqa: ANN001 - polars' FileProviderArgs is unstable
        bucket = args.partition_keys.get_column(BUCKET)[0]
        return f"{BUCKET}={bucket}/{tag}-{args.index_in_partition:04d}.parquet"

    (
        frame.lazy()
        .with_columns((pl.col(ENCOUNTER_ID).hash() % buckets).alias(BUCKET))
        .sort(BUCKET, ENCOUNTER_ID, time_col)
        .sink_parquet(
            pl.PartitionBy(
                str(directory), key=BUCKET, include_key=False, file_path_provider=file_name
            ),
            maintain_order=True,
        )
    )


def scan_long(directory: Path, schema: dict[str, pl.DataType]) -> pl.LazyFrame:
    if not directory.exists() or not any(directory.rglob("*.parquet")):
        return pl.LazyFrame(schema=schema)
    return pl.scan_parquet(directory / "**" / "*.parquet").select(list(schema))


def _replace_dir(final: Path, tmp: Path) -> None:
    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)


def save_cohort(cohort: Cohort, path: Path | str) -> Path:
    """Write a cohort to ``path``; safe to call on the directory it was loaded from."""
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    buckets = n_buckets(len(cohort))

    # Observations go to temporary directories first, so that saving in place never
    # reads from files that are being replaced.
    for name, frame, time_col in (
        (DYNAMIC_DIR, cohort.dynamic, TIME),
        (EVENTS_DIR, cohort.events, TIME),
        (INTERVALS_DIR, cohort.intervals, START),
    ):
        tmp = root / f"{name}.tmp"
        if tmp.exists():
            shutil.rmtree(tmp)
        sink_long(frame, tmp, buckets, time_col=time_col)
        _replace_dir(root / name, tmp)

    cohort.static.write_parquet(root / STATIC_FILE)
    cohort.config.save(root / COHORT_FILE)
    cache = root / DERIVED_DIR / Cohort.FEATURES_CACHE
    cache.unlink(missing_ok=True)
    cohort.dynamic = scan_long(root / DYNAMIC_DIR, LONG_SCHEMA)
    cohort.events = scan_long(root / EVENTS_DIR, EVENT_SCHEMA)
    cohort.intervals = scan_long(root / INTERVALS_DIR, INTERVAL_SCHEMA)
    return root


def write_cohort_batches(
    path: Path | str,
    config: CohortConfig,
    batches: Iterable[tuple],
    n_encounters: int,
) -> Path:
    """Write a cohort from an iterator of ``(static, dynamic, events[, intervals])`` batches.

    Used for sources too large to hold in memory (synthetic generation, chunked imports).
    """
    root = Path(path)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    buckets = n_buckets(n_encounters)
    statics = []
    for i, batch in enumerate(batches):
        static, dynamic, events = batch[:3]
        intervals = batch[3] if len(batch) > 3 else None
        statics.append(static)
        sink_long(dynamic, root / DYNAMIC_DIR, buckets, tag=f"b{i:05d}")
        if events is not None and events.height:
            sink_long(events, root / EVENTS_DIR, buckets, tag=f"b{i:05d}")
        if intervals is not None and intervals.height:
            sink_long(intervals, root / INTERVALS_DIR, buckets, tag=f"b{i:05d}", time_col=START)
    static = pl.concat(statics, how="diagonal_relaxed").sort(ENCOUNTER_ID)
    static.write_parquet(root / STATIC_FILE)
    config.save(root / COHORT_FILE)
    return root


def load_cohort(path: Path | str) -> Cohort:
    root = Path(path)
    if not is_cohort_dir(root):
        raise FileNotFoundError(f"{root} is not a DEA cohort (missing {COHORT_FILE}/{STATIC_FILE})")
    config = CohortConfig.load(root / COHORT_FILE)
    static = pl.read_parquet(root / STATIC_FILE)
    dynamic = scan_long(root / DYNAMIC_DIR, LONG_SCHEMA)
    events = scan_long(root / EVENTS_DIR, EVENT_SCHEMA)
    intervals = scan_long(root / INTERVALS_DIR, INTERVAL_SCHEMA)
    return Cohort(static, dynamic, events=events, intervals=intervals, config=config, root=root)


def delete_cohort(path: Path | str) -> None:
    """Remove a cohort directory. Refuses anything that is not a DEA cohort."""
    root = Path(path)
    if not is_cohort_dir(root):
        raise FileNotFoundError(f"{root} is not a DEA cohort")
    shutil.rmtree(root)


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

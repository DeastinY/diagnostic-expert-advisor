# Architecture

DEA v2 is one Python package with five layers. Every layer is usable without the ones
above it, so a researcher can use the data model in a notebook without ever starting the
UI, and the UI never contains logic that the library does not expose.

```
dea.ui        Streamlit app: cohorts & import, overview, encounters, profile & advice, compute
dea.advise    optional AI advisor (OpenAI-compatible); input = CohortProfile only
dea.profile   deterministic profiling: sampling, coverage, distributions, flags
dea.compute   Backend protocol: serial, local process pool, SLURM (submitit)
dea.io        importers (long/wide tables, DEA v1 CSV tree) + Parquet storage
dea.core      Encounter, Cohort, CohortConfig on Polars
dea.synth     synthetic ICU cohorts for demos and tests
```

## Data model

Observations live in **long format**: `encounter_id | time | variable | value`, where
`time` is a Duration since admission and `value` is a float. This is the representation
that makes heterogeneous clinical data cheap: encounters record different variables at
different rates, nothing is padded, and Polars can scan the whole cohort lazily from
hive-partitioned Parquet.

Wide frames (one column per variable) are produced on demand by `Encounter.wide()` and
`Cohort.wide()`, optionally resampled onto a grid (`every="15m"`) with an aggregate and a
gap-filling strategy. Those defaults live in `cohort.yaml` under `processing` and can be
overridden per call.

Static, per-encounter facts live in one table keyed by `encounter_id`. IDs are always
strings; nothing relies on row order.

## Configuration instead of code

`cohort.yaml` holds everything domain specific: filters, tags, default plots, labels,
units and processing. Filters and tags are SQL expressions evaluated against the
**encounter feature table** (`Cohort.features()`): static columns, `los_hours`,
`n_observations`, and `<variable>__min|max|mean|first|last|count`. The same table is a
ready-made encounter-level feature matrix for modelling.

## Storage layout

```
<cohort>/
  cohort.yaml
  static.parquet
  dynamic/bucket=NN/*.parquet     numeric observations
  events/bucket=NN/*.parquet      string observations
  derived/<name>.parquet          job results and the cached feature table
```

Observations are spread over at most 256 hash buckets (about 500 encounters each) and every
file is sorted by encounter and time. Each encounter sits in exactly one file, so a single
encounter is read by skipping row groups rather than scanning, and per-encounter
aggregations (first/last values, sampling gaps) never need a global sort. All writes are
streaming sinks.

## Why Parquet and Polars, not a time-series database

Measured on a synthetic cohort of 2,000 encounters and 54 million observations
(150 MB on disk, 40 files):

| Operation | Time | Peak memory |
|---|---|---|
| open cohort | 0.02 s | - |
| fetch one encounter (57k observations) | 0.02 s | - |
| encounter feature table (2,000 × 143) | ~1 s | 0.5 GB |
| wide export on a 1 h grid, all encounters | 4.6 s | bounded per bucket |

Everything scales linearly in the number of buckets, and a MIMIC-IV-sized cohort
(70k stays, ~330M observations) is about 150 buckets. A time-series database (TimescaleDB,
QuestDB, InfluxDB, ClickHouse) would add a server to run and secure, a network hop from every
cluster node, and an ETL step before the first chart, while Parquet files on a shared
filesystem are readable by every HPC job, every notebook and DuckDB alike. The trade-off is
that DEA holds one cohort per directory rather than one global queryable store, which is the
right shape for a demonstrator and for cohort-level research.

## Compute

`Cohort.apply(fn, backend)` runs `fn(encounter)` everywhere and collects dicts or
DataFrames into one table. Backends share a one-method contract (`map`). The SLURM
backend wraps `submitit` array jobs and has not yet been exercised on a real cluster.

## Privacy boundary

The advisor's only input type is `CohortProfile`. The payload consists of counts,
sampling statistics and per-column aggregates; categorical labels are included only for
low-cardinality static columns where every shown category covers at least five
encounters. The UI shows the exact payload before anything is sent.

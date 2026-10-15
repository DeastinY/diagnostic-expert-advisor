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
  dynamic/encounter_id=<id>/*.parquet
  derived/<name>.parquet
```

## Compute

`Cohort.apply(fn, backend)` runs `fn(encounter)` everywhere and collects dicts or
DataFrames into one table. Backends share a one-method contract (`map`). The SLURM
backend wraps `submitit` array jobs and has not yet been exercised on a real cluster.

## Privacy boundary

The advisor's only input type is `CohortProfile`. The payload consists of counts,
sampling statistics and per-column aggregates; categorical labels are included only for
low-cardinality static columns where every shown category covers at least five
encounters. The UI shows the exact payload before anything is sent.

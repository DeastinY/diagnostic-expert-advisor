# Diagnostic Expert Advisor (DEA)

[![CI](https://github.com/JRC-COMBINE/DEA/actions/workflows/ci.yml/badge.svg)](https://github.com/JRC-COMBINE/DEA/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)
![License](https://img.shields.io/github/license/jrc-combine/dea)

DEA turns heterogeneous clinical time-series data into one structure and gives you
profiles, suggestions, views and analysis-ready frames in minutes.

- **For clinicians:** drop in a table or a folder of CSVs, get a cohort overview, per-encounter
  views and a data-quality profile without writing code.
- **For researchers:** the same cohort as a Polars or pandas frame, an encounter-level feature
  matrix, configurable filters, and one `apply()` call that runs locally today and on a SLURM
  cluster tomorrow.
- **Optional AI advisor:** recommendations on how to resample, fill and store the data, from
  any OpenAI-compatible endpoint, computed from aggregate statistics only.


> DEA v2 is a clean rewrite: Polars and Parquet underneath, a FastAPI backend and a React
> frontend on top. The v1 Flask application lives in the git history; its CSV layout can still
> be imported.

## Install

```bash
pip install "dea[all] @ git+https://github.com/JRC-COMBINE/DEA.git"
# or, from a clone:
pip install -e ".[all]"
```

Extras: `web` (FastAPI backend serving the web app), `ai` (advisor over any OpenAI-compatible
endpoint), `report` (offline HTML reports with embedded SVG charts), `nlp-spacy`, `nlp-gliner`,
`nlp-transformers`, `deid` (local NLP connectors), `pandas` (pandas interop), `slurm` (cluster
backend). The core needs only Polars, pydantic
and PyYAML.

## Five-minute tour

```bash
dea synth data/demo --n 60      # a synthetic ICU cohort; no real patients involved
dea suggest data/demo           # harmonization, processing, tags, quality findings
dea serve --data-dir data       # web app at http://127.0.0.1:8000
```

Have your own data? `dea detect my_export.csv` shows what DEA recognizes, then

```bash
dea import my_export.csv data/my_cohort            # long or wide tables, csv/tsv/parquet
dea import /path/to/mimic-iv-demo data/mimic       # MIMIC-IV layout (icu/, hosp/, note/)
dea import /path/to/v1/cohort data/migrated        # the DEA v1 static.csv + <id>/dynamic.csv tree
```


## The web app

`dea serve` starts a FastAPI backend and serves the React frontend (built into
`frontend/dist`; run `npm install && npm run build` in `frontend/` from a checkout).

- **Cohorts**: cards with lineage, import from a path or an upload with layout detection and
  editable column mapping, or a demo cohort (optionally in MIMIC-IV layout).
- **Overview**: stay lengths, coverage, tags, static and variable distributions, and
  **trajectories** (median and interquartile band since admission, split by any static column).
  Filters live in the URL, so every view is shareable.
- **Encounters**: sortable, searchable list with tags; per-encounter small multiples and events.
- **Build cohort**: condition builder over the feature catalog with live counts, save as filter
  or tag, carve out a **sub-cohort** with its parent and rule recorded.
- **Profile & suggestions**: sampling, coverage, distributions, red flags; grouped one-click
  suggestions (harmonize names, move static attributes, processing defaults, clinical tags,
  filters, charts, data quality); processing editor; optional AI advisor; entities from NLP.
- **Report** and **Parquet export** of any filtered view.

## MIMIC-IV

`dea import <mimic-root>` reads the PhysioNet layout (`icu/icustays`, `icu/chartevents`,
`icu/d_items`, `hosp/patients`, `hosp/admissions`, `hosp/labevents`, `hosp/d_labitems`,
`hosp/diagnoses_icd`, `hosp/d_icd_diagnoses`, optional `note/discharge`): one encounter per
ICU stay, items named by their dictionary labels, text values as events, labs attached to the
stay window, per-row units (`valueuom`) normalized, ICD-9/ICD-10 diagnoses as `dx_<group>`
columns. `dea synth out --mimic --n 50` writes synthetic tables in the same layout (including
MIMIC's °F/°C temperature items and mixed ICD versions) for development without credentials.

## To a model

`dea ml data/icu out.parquet --label "Severe ARDS" --cutoff 24 --split site` writes one row per
encounter with features from before the cutoff only, a whole-stay label and a split that holds
out whole hospitals; outcome and label columns are dropped. The same lives in the web app's
cohort builder and in `dea.ml.make_dataset`. See [`docs/ml.md`](docs/ml.md).

## Public data

Tested on DEA's own synthetic cohorts and on the SMART on FHIR sample server data (631 Synthea
patient bundles, FHIR R4, 30+ organizations). [`docs/datasets.md`](docs/datasets.md) lists the
open ICU datasets (MIMIC-IV demo, eICU, HiRID, PhysioNet challenges) and how each one feeds in.

## Checks and harmonization

Every import runs data quality checks before anything is written: mixed units (converted on
the spot, with a report), values outside hard physiological limits with the unit that would
explain them, two-cluster distributions, duplicates, time offsets before admission or months
after it, empty encounters, numbers stored as text. `dea check <cohort>`, the "Check first"
button on the import form and the profile page show them; fixes apply with one click and are
recorded in `cohort.yaml`.

Pooled cohorts get a site dimension: per-site distributions, coverage and sampling grids are
compared on the overview page, and site-specific units, shifts and missing variables become
findings with site-restricted fixes (`dea sites <cohort>`, `dea synth out --sites 3` for a demo).

Measurements harmonize through the dictionary's LOINC and UCUM codes, diagnoses through
cross-version ICD groups (`dx_sepsis`, `dx_heart_failure`, ...), and cohorts import from and
export to FHIR R4 (`dea export <cohort> <dir> --fhir`). See
[`docs/harmonization.md`](docs/harmonization.md).

## Free text: local NLP connectors

```bash
dea nlp list                                      # which connectors are installed
dea nlp install spacy --model en_core_web_sm      # models go to DEA_MODELS_DIR (~/.dea/models)
dea nlp run data/demo --connector spacy --events Note
dea nlp run data/demo --connector gliner --labels disease medication procedure
dea nlp run data/demo --connector presidio        # PII detection; redaction via dea.nlp.redact_cohort
```

Connectors: spaCy (any pipeline, including scispaCy and medspaCy), GLiNER zero-shot NER,
Hugging Face token classification, Presidio de-identification, and any OpenAI-compatible local
LLM such as Ollama. All run locally; entities land in `derived/entities.parquet`, feed the
Entities tab, and can become `mentions: <entity>` tags. Note that the default `torch` wheel is
the CUDA build; install the CPU wheel from the PyTorch index if you do not have a GPU.

## Sharing an instance

`dea serve` is single-user by default. Set `DEA_TOKEN=<secret>` to require a bearer token on
every API call (the web app asks for it once and remembers it in the browser); put a TLS
proxy in front for anything beyond a trusted network. The report (`dea report`) now carries
the data quality checks, the per-site case mix and findings, and the sub-cohorts derived from
a cohort, and the encounter view draws the cohort's median and interquartile band behind each
patient's series.

## In Python

```python
import dea

cohort = dea.load_cohort("data/demo")
cohort.summary()                       # encounters, variables, observations, length of stay
cohort.features()                      # one row per encounter: static + <variable>__min|max|mean|...
severe = cohort.filter('"Horowitz__min" < 100 AND los_hours > 24')

enc = cohort.get("ICU-2021-48213")
enc.wide(every="15m", fill="forward")  # time x variables, Polars
enc.series("MAP")                      # raw observations of one variable

frame = cohort.wide(every="1h", fill="forward", with_static=True)   # for ML, all encounters
pdf = frame.to_pandas()                                             # or stay in Polars

from dea.compute import LocalBackend
cohort.apply(my_function, backend=LocalBackend(), name="my_result")  # -> derived/my_result.parquet

cohort.trajectory("MAP", every="4h", by="outcome")        # cohort-level course of a variable
cohort.export_wide("wide/", every="1h", fill="forward")  # bucket-by-bucket, bounded memory
cohort.rename_variables({"HF": "heart_rate"}).save()     # harmonize names; config follows
```

## How it fits together

| Layer | What it does |
|---|---|
| `dea.core` | `Encounter`, `Cohort`, `CohortConfig` on Polars; long-format observations, static table, resampling |
| `dea.io` | detect & import long/wide tables and the v1 CSV tree; Parquet storage |
| `dea.profile` | deterministic profiling: sampling intervals, coverage, distributions, red flags |
| `dea.compute` | `SerialBackend`, `LocalBackend`, `SlurmBackend` behind one `map()` contract |
| `dea.advise` | optional advisor over an OpenAI-compatible API; input is the profile, never rows |
| `dea.ui` | Streamlit app: Cohorts, Overview, Encounters, Profile & Advice, Compute |
| `dea.synth` | synthetic ICU cohorts for demos and tests |

Everything domain specific (filters such as *Severe ARDS*, tags, default plots, labels, units,
processing defaults) lives in the cohort's `cohort.yaml`, not in code:

```yaml
filters:
  - label: Severe ARDS
    where: '"Horowitz__min" < 100'
processing:
  resample_every: 15m
  aggregate: mean
  fill: forward
```

More in [`docs/architecture.md`](docs/architecture.md), [`docs/data-formats.md`](docs/data-formats.md),
[`docs/ai-advisor.md`](docs/ai-advisor.md) and [`docs/hpc.md`](docs/hpc.md).

## AI advisor

```bash
export DEA_AI_BASE_URL=http://localhost:11434/v1   # e.g. Ollama; or leave unset for OpenAI
export DEA_AI_MODEL=llama3.1
dea advise data/demo --show-payload                # see exactly what would be sent
dea advise data/demo --apply                       # write the recommendation into cohort.yaml
```

The advisor receives `CohortProfile.to_prompt_dict()`: counts, sampling statistics and
per-column aggregates. No encounter ids, timestamps or individual values ever leave the
machine, and nothing changes until you apply a recommendation.

## Development

```bash
pip install -e ".[dev]"
ruff check src tests && ruff format --check src tests
pytest
```

## Status

Alpha (2.0.0a2). Tested on synthetic data up to 2,000 encounters / 54 million observations
(feature table in ~3 s, profile in ~4 s, single-encounter lookup in 20 ms, all under 2.5 GB),
on synthetic MIMIC-IV-shaped tables, and on the SMART on FHIR sample server data (631 Synthea
patient bundles, 30+ organizations); storage is designed for MIMIC-sized cohorts, see
[`docs/architecture.md`](docs/architecture.md).

Still to validate, in order of value: a real ICU dataset (the free MIMIC-IV and eICU demos
need a PhysioNet account, which the development sandbox could not reach), the SLURM backend on
a real cluster, the GLiNER and transformer NLP connectors with downloaded weights. The ICD
group definitions, the unit conversion table and the drug list are small and meant to be
extended per site.

## License

GPL-3.0, see [LICENSE.md](LICENSE.md).

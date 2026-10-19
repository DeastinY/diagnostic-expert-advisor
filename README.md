# Diagnostic Expert Advisor (DEA)

[![CI](https://github.com/DeastinY/diagnostic-expert-advisor/actions/workflows/ci.yml/badge.svg)](https://github.com/DeastinY/diagnostic-expert-advisor/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)
![License](https://img.shields.io/badge/license-GPLv3-blue)

**Project page:** https://deastiny.github.io/diagnostic-expert-advisor/

The DEA is the fastest way to get a look at your data. It works as a platform for researchers and 
clinicians alike, enabling easy data ingestion, fast visualization, and efficient computation.  
It is built as a toolkit to enable consistent and reliable workflows for new data, while tackling 
recurring issues in scale and integrating with HPC. Some features to note are:

- **Guided Data Import:** Just throw in your folder of csv files, read FHIR resources, or parquet files. 
  Data is conveniently imported, harmonized, and efficiently stored.
- **Automated Checks:** Mixed units, values out of range, duplicates, numbers stored as
  text, timestamps before admission and differences between hospitals are reported before
  anything is written. Automatic fixes are available for many issues, everything is transparently audited.
- **Plots without plotting:** With a focus on clinical timeseries data there are a few assumptions about 
  interesting plots one can make. We offer a lot of out-of-the-box visualizations without even touching code.
- **Harmonization across Standards:** Support for variable mapping to LOINC and UCUM codes, diagnoses are grouped across ICD-9 and
  ICD-10, drugs get RxNorm and ATC. Local naming goes into a mapping file. Everything reproducible and explainable.
- **Datasets for research:** Easily access data in a machine learning friendly `(X, y)` representation. Including prediction cutoffs,
  leakage guards and a splitting by encounter, patient or whole hospital. Build subcohorts and keep track of your experiments.
- **Optional AI advisor** on any OpenAI-compatible endpoint and MCP for attaching local AI to the DEA. The sky is the limit and 
  future will show what is possible with this.

Everything runs locally on Polars and Parquet. A MIMIC-sized cohort fits on a laptop.

## Install

We use [uv](https://docs.astral.sh/uv/), pip works as well. You need Python 3.11 or newer.

```bash
git clone https://github.com/DeastinY/diagnostic-expert-advisor.git dea && cd dea
uv sync --all-extras          # core + web app, advisor, reports, NLP, pandas, SLURM
uv run dea --help
```

Without a clone, as a tool on your PATH:

```bash
uv tool install "dea[all] @ git+https://github.com/DeastinY/diagnostic-expert-advisor"
dea serve --data-dir ~/dea-data
```

With pip: `pip install -e ".[all]"` from the clone, or `pip install -e ".[web]"` if you only
want the app. The core itself needs just Polars, pydantic and PyYAML.

The web app is committed as a built bundle in `frontend/dist` and shipped inside the package,
so neither way needs node. If you change the frontend, rebuild it with `npm ci && npm run
build` inside `frontend/`.

## Update

```bash
uv run dea update          # a clone: git pull + uv sync. A tool install: uv tool upgrade dea
uv run dea update --check  # only tell me whether there is something new
uv run dea serve --auto-update   # update first, then start (or DEA_AUTO_UPDATE=1)
```

`dea version --check` shows the install mode and commit. The web app shows a hint in the
sidebar when the repository has moved on. `DEA_NO_UPDATE_CHECK=1` turns the check off.

## Get started

Start the server and do the rest in the browser:

```bash
uv run dea serve --data-dir data      # http://127.0.0.1:8000
```

Drop a folder or a file anywhere on the page (a MIMIC-IV download, FHIR resources, a CSV or
Parquet table, a zip) and the guided import takes over: it shows what was recognized, runs the
checks without writing anything, lets you tick the fixes and check again, imports on your go,
and can apply the suggested harmonization in the same step. Afterwards the overview, the distributions and the patient pages are ready.
Large folders that are already on the machine can be pasted as a path instead of uploading
them. The *Demo* tab generates a synthetic cohort over three hospitals if you have no data at
hand.

The same from the command line:

```bash
uv run dea synth data/demo --n 60 --sites 3   # synthetic cohort over three hospitals
uv run dea detect my_export.csv                # what is recognized: layout, columns, time unit
uv run dea import my_export.csv data/mine      # runs the checks, then writes the cohort
uv run dea check data/mine                     # the findings again, any time
uv run dea suggest data/mine --apply           # harmonization, tags, processing defaults
```

Step-by-step instructions for your own data, for MIMIC-IV (including how to get access) and
for FHIR are in [`docs/getting-started.md`](docs/getting-started.md).

## MIMIC-IV

```bash
# the demo needs a free PhysioNet account, the full database credentialing (see the guide)
wget -r -N -c -np --user YOU --ask-password https://physionet.org/files/mimic-iv-demo/2.2/
uv run dea serve --data-dir data
```

Then drop the downloaded `2.2` folder onto the page (or paste its path, or run
`uv run dea import physionet.org/files/mimic-iv-demo/2.2 data/mimic`). The importer reads
`icu/` (stays, chart, input, procedure and output events), `hosp/` (patients, admissions,
labs, diagnoses) and `note/`. Units are converted per row, the °F and °C temperature items end
up in one variable and ICD-9 and ICD-10 diagnoses become `dx_<group>` columns. The full
database works the same way, its large gzipped tables are converted to Parquet once.

## Python

```python
from dea.io import load_cohort
from dea.ml import make_dataset

cohort = load_cohort("data/mimic")
cohort.features()                         # one row per encounter, aggregates + static columns
cohort.filter('"lactate__max" > 2')       # any SQL rule over that table
ds = make_dataset(cohort, label="Severe ARDS", cutoff_hours=24, split="site")
X, y = ds.train()
```

`cohort.wide()` gives the resampled time series as a Polars (or pandas) frame and
`cohort.apply(fn)` runs per-encounter code locally or on SLURM.

## Workshops and support

Together with the Simulation and Data Lab Digital Patient (SDL DP) of
[NHR4CES](https://www.nhr4ces.de/) we run regular workshops on working with the DEA, give
personal demonstrations on your own data and help with the setup, from a laptop to the SLURM
backend on the cluster. Open an issue here or get in touch with the SDL Digital Patient.

## Documentation

| | |
|---|---|
| [getting-started.md](docs/getting-started.md) | own data, MIMIC-IV access and import, FHIR, several hospitals |
| [data-formats.md](docs/data-formats.md) | the long data model, intervals, the Parquet layout, config |
| [harmonization.md](docs/harmonization.md) | checks, units, LOINC, ICD groups, mapping files, FHIR, sites |
| [ml.md](docs/ml.md) | datasets for modelling: label, cutoff, split, leakage guard |
| [architecture.md](docs/architecture.md) | storage decisions, benchmarks, result cache |
| [ai-advisor.md](docs/ai-advisor.md), [nlp.md](docs/nlp.md), [hpc.md](docs/hpc.md) | advisor, local NLP connectors, SLURM |
| [datasets.md](docs/datasets.md) | public datasets and what each one exercises |
| [deastiny.github.io/diagnostic-expert-advisor](https://deastiny.github.io/diagnostic-expert-advisor/) | project page, deployed from `docs/` by the Pages workflow |

## Development

```bash
uv sync --all-extras
uv run pytest -q              # about a minute
uv run ruff check src tests && uv run ruff format src tests
DEA_TOKEN=secret uv run dea serve   # bearer-token protection for a shared instance
```

## Status

Alpha (2.0.0a2). So far tested on synthetic cohorts up to 2,000 encounters and 54 million
observations, on synthetic tables in the MIMIC-IV layout and on the SMART on FHIR sample data
(631 Synthea patients, 30+ organizations). Not yet tested: a real ICU dataset, the SLURM
backend on a real cluster, the GLiNER and transformer NLP connectors with downloaded weights.

## License

GPL-3.0, see [LICENSE.md](LICENSE.md).

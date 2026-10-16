# Diagnostic Expert Advisor (DEA)

[![CI](https://github.com/DeastinY/diagnostic-expert-advisor/actions/workflows/ci.yml/badge.svg)](https://github.com/DeastinY/diagnostic-expert-advisor/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)
![License](https://img.shields.io/badge/license-GPLv3-blue)

**Project page:** https://deastiny.github.io/diagnostic-expert-advisor/

The DEA is a lightweight toolkit for working with clinical time series. You point it at a data
export, a MIMIC-IV folder or FHIR resources, it figures out the layout, checks the data for the
usual problems and gives you plots for the whole cohort, every hospital and every patient. Most
of this happens in the browser. If you prefer scripts, the same steps are available on the
command line and in Python.

- **Import through the UI.** CSV and Parquet tables (long or wide), MIMIC-IV folders, FHIR R4
  bundles and the v1 CSV tree are recognized, including which column is what and the units.
- **Checks run on their own.** Mixed units, values out of range, duplicates, timestamps before
  admission and differences between hospitals are reported before anything is written. Fixes
  are one click and stored with the cohort.
- **Plots without plotting.** Cohort overview, per-hospital comparison, one page per patient
  with the cohort median and IQR behind each signal, data-quality profile, HTML report.
- **Harmonization.** Variables get LOINC and UCUM codes, diagnoses are grouped across ICD-9 and
  ICD-10, drugs get RxNorm and ATC. Local naming goes into a mapping file.
- **Datasets for models.** One call gives an encounter-level `(X, y)` with a prediction cutoff,
  a leakage guard and a split by encounter, patient or whole hospital.
- **Optional AI advisor** on any OpenAI-compatible endpoint. It only ever sees aggregates.

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

On the landing page, *Add data* lets you create a demo cohort (three synthetic hospitals) with
one click, or paste the path to your own export and hit *Check first*. That shows what the
importer recognized and what the checks found without writing anything. *Import* then writes
the cohort, and the overview, the distributions and the patient pages are ready.

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

Then paste `physionet.org/files/mimic-iv-demo/2.2` into *Add data*, or run
`uv run dea import physionet.org/files/mimic-iv-demo/2.2 data/mimic`. The importer reads
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

# Diagnostic Expert Advisor (DEA)

[![CI](https://github.com/DeastinY/diagnostic-expert-advisor/actions/workflows/ci.yml/badge.svg)](https://github.com/DeastinY/diagnostic-expert-advisor/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)
![License](https://img.shields.io/badge/license-GPLv3-blue)

**Project page:** https://deastiny.github.io/diagnostic-expert-advisor/

A workbench for clinical time series. Import hospital data in whatever layout it comes in,
get it checked and harmonized to standards, look at every patient and every site, and hand a
clean table to your models.

- **Import anything:** CSV/Parquet tables (long or wide), MIMIC-IV, FHIR R4 bundles, the v1 CSV tree.
- **Check and harmonize:** mixed units, implausible values, duplicates and site differences are
  caught before anything is written. Variables map to LOINC/UCUM, diagnoses to ICD-9/ICD-10
  groups, drugs to RxNorm/ATC.
- **Look:** cohort overview, per-site comparison, per-encounter time series with the cohort
  band behind them, data-quality profile, HTML report.
- **Model:** one call gives an encounter-level `(X, y)` with a prediction cutoff, a leakage guard
  and a split by encounter, patient or whole hospital.
- **Optional AI advisor** on any OpenAI-compatible endpoint. It only ever sees aggregate statistics.

Everything runs locally on Polars and Parquet; a MIMIC-sized cohort fits a laptop.

## Install

With [uv](https://docs.astral.sh/uv/) (recommended):

```bash
git clone https://github.com/DeastinY/diagnostic-expert-advisor.git dea && cd dea
uv sync --all-extras          # core + web app, advisor, reports, NLP, pandas, SLURM
uv run dea --help
```

With pip: `pip install -e ".[all]"` from the clone, or just `pip install -e ".[web]"` for the
app. The core needs only Polars, pydantic and PyYAML.

The web frontend is prebuilt in `frontend/dist`; rebuild it with `npm ci && npm run build`
inside `frontend/` after changing it.

## Get started

```bash
uv run dea synth data/demo --n 60 --sites 3   # synthetic ICU cohort over three hospitals
uv run dea serve --data-dir data               # http://127.0.0.1:8000
```

With your own export:

```bash
uv run dea detect my_export.csv                # what DEA recognizes: layout, columns, time unit
uv run dea import my_export.csv data/mine      # runs the checks, writes the cohort
uv run dea check data/mine                     # findings again, any time
uv run dea suggest data/mine --apply           # harmonization, tags, processing defaults
```

Step-by-step instructions for your own data, for MIMIC-IV (including how to get access) and
for FHIR are in [`docs/getting-started.md`](docs/getting-started.md).

## MIMIC-IV in three commands

```bash
# after PhysioNet credentialing, see docs/getting-started.md
wget -r -N -c -np --user YOU --ask-password https://physionet.org/files/mimic-iv-demo/2.2/
uv run dea import physionet.org/files/mimic-iv-demo/2.2 data/mimic
uv run dea serve --data-dir data
```

The importer reads `icu/` (stays, chart, input, procedure and output events), `hosp/`
(patients, admissions, labs, diagnoses) and `note/`; units are normalized per row, °F and °C
items merge, ICD-9 and ICD-10 diagnoses become `dx_<group>` columns. Full MIMIC-IV works the
same way; large gzipped tables are converted to Parquet once.

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

`cohort.wide()` gives the resampled time series as a Polars (or pandas) frame;
`cohort.apply(fn)` runs per-encounter code on the local or SLURM backend.

## Documentation

| | |
|---|---|
| [getting-started.md](docs/getting-started.md) | own data, MIMIC-IV access and import, FHIR, uv workflow |
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
uv run pytest -q              # ~1 min
uv run ruff check src tests && uv run ruff format src tests
DEA_TOKEN=secret uv run dea serve   # bearer-token protection for a shared instance
```

## Status

Alpha (2.0.0a2). Validated on synthetic cohorts up to 2,000 encounters / 54 million
observations, on synthetic MIMIC-IV-shaped tables, and on the SMART on FHIR sample data
(631 Synthea patients, 30+ organizations). Still to validate: a real ICU dataset, the SLURM
backend on a cluster, the GLiNER/transformer NLP connectors with downloaded weights.

## License

GPL-3.0, see [LICENSE.md](LICENSE.md).

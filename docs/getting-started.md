# Getting started

## Install

```bash
git clone https://github.com/DeastinY/diagnostic-expert-advisor.git dea && cd dea
uv sync --all-extras                 # or: uv sync --extra web --extra ai
uv run dea --help
```

`uv run` executes inside the project environment, there is nothing to activate. `uv sync`
without extras installs only the core (Polars, pydantic, PyYAML) plus the dev tools. pip works
too: `pip install -e ".[all]"`.

## Start the app

```bash
uv run dea serve --data-dir data
```

Open http://127.0.0.1:8000. The data directory holds one folder per cohort and is created if
it does not exist. Everything below can be done from this page; the command line equivalents
are listed for scripts.

## Your own data

In the browser: *Add data*, paste the path to your file or folder (or upload a file), and
*Check first*. The DEA shows what it recognized (layout, columns, time unit, units) and what
the checks found, without writing anything. *Import* writes the cohort. From there the
overview, the distributions, the profile page with its suggestions and the patient pages are
ready.

The same from the command line:

1. `uv run dea detect export.csv` prints the detected layout (long or wide), the encounter,
   time, variable, value and unit columns and the time unit. Everything it guessed can be
   overridden: `--encounter-col`, `--time-col`, `--variable-col`, `--value-col`, `--unit-col`,
   `--time-unit {datetime,s,m,h,d}`, `--table-kind {long,wide}`.
2. `uv run dea import export.csv data/mine --static patients.csv` runs the checks first
   (mixed units, values out of range, duplicates, timestamps before admission), prints them
   and then writes the cohort. Nothing is written if the import fails. `--mapping site.yaml`
   applies your local-name-to-concept mapping on the way (see `harmonization.md`).
3. `uv run dea check data/mine` repeats the checks. `uv run dea suggest data/mine --apply`
   renames recognized variables to canonical names, moves constant attributes to the static
   table, sets processing defaults and adds clinical tags. In the app each suggestion can be
   applied one by one.

What a table needs: one encounter id column, one time column (timestamps or offsets since
admission) and either `variable`/`value` columns (long) or one column per variable (wide).
Non-numeric values become events, string columns of a static table become filters.

Folders with several tables, the DEA v1 `static.csv` + `<id>/dynamic.csv` tree and zip uploads
are detected the same way.

## MIMIC-IV

### Getting access

MIMIC-IV is hosted on PhysioNet. The **demo** (100 patients, every table) only needs a free
PhysioNet account. The **full database** needs credentialing:

1. Create an account at https://physionet.org/register/.
2. Complete the CITI course "Data or Specimens Only Research" (free, takes a few hours) and
   upload the completion report to your PhysioNet profile.
3. Submit the credentialing application on PhysioNet. A reference is required and it takes a
   week or two.
4. Sign the data use agreement on the MIMIC-IV project page:
   https://physionet.org/content/mimiciv/.

### Download

```bash
# demo (after login, about 15 MB)
wget -r -N -c -np --user YOU --ask-password https://physionet.org/files/mimic-iv-demo/2.2/
# full (credentialed, about 7 GB compressed)
wget -r -N -c -np --user YOU --ask-password https://physionet.org/files/mimiciv/3.1/
# clinical notes (a separate credentialed project, optional)
wget -r -N -c -np --user YOU --ask-password https://physionet.org/files/mimic-iv-note/2.2/
```

Keep the folder layout as downloaded, with `icu/` and `hosp/` (and `note/` if you have it)
side by side. Both `.csv` and `.csv.gz` are read.

### Import

Paste `physionet.org/files/mimic-iv-demo/2.2` into *Add data* and hit *Check first*, or:

```bash
uv run dea import physionet.org/files/mimic-iv-demo/2.2 data/mimic-demo
uv run dea import physionet.org/files/mimiciv/3.1 data/mimic --name mimic
```

You get one encounter per ICU stay (`stay_id`) with time as offset from `intime`. The importer
reads `icustays`, `chartevents`, `d_items`, `patients`, `admissions`, `labevents` (attached to
the stay window), `diagnoses_icd` (ICD-9 and ICD-10 as `dx_<group>` columns), `inputevents`
and `procedureevents` (as intervals), `outputevents`, and discharge and radiology notes as
events. Units are read per row and converted to one unit per variable, so `Temperature
Fahrenheit` arrives in °C and `Inspired O2 Fraction` as a fraction. The import report lists
what was converted.

For the full database the first import converts the large gzipped tables (`chartevents`,
`labevents`, `inputevents`) to Parquet parts in a cache directory (`$DEA_CACHE_DIR`, otherwise
`.dea_cache` next to the data, otherwise the temp directory). That takes a few minutes, every
later import reuses the parts. Memory stays bounded.

Afterwards the profile page (or `uv run dea suggest data/mimic`) offers the harmonization
renames (`Heart Rate` to `heart_rate`, both temperature items to `temperature`, ...) and tags.

## FHIR

A folder of FHIR R4 NDJSON files (bulk export) or JSON bundles imports like any other folder:

```bash
uv run dea import ./fhir-export data/fhir
```

Patient, Encounter, Observation (LOINC coded, components flattened), Condition (ICD or SNOMED)
and MedicationAdministration are read, Organizations become the site column. `uv run dea
export data/fhir out/ --fhir` writes a cohort back as FHIR. Free test data:
`git clone --depth 1 --filter=blob:none --sparse https://github.com/smart-on-fhir/generated-sample-data`
followed by `git sparse-checkout set R4`.

## Several hospitals

If a static column names the hospital or site (`site`, `hospital`, `center`, `provider`, ...)
the DEA picks it up, otherwise you can set it on the overview page. The site comparison then
shows case mix, per-site distributions, coverage and sampling grids, and flags unit, shift and
coverage differences with fixes that only apply to one site. `uv run dea sites data/mine`
prints the same.

## Datasets for models

The dataset card on the cohort builder page, or:

```bash
uv run dea ml data/mimic ds.parquet --label "Severe ARDS" --cutoff 24 --split site
```

See `ml.md` for what the label, cutoff, split and leakage guard do.

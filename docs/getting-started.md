# Getting started

## Install with uv

```bash
git clone https://github.com/DeastinY/diagnostic-expert-advisor.git dea && cd dea
uv sync --all-extras                 # or: uv sync --extra web --extra ai
uv run dea --help
```

`uv run` executes inside the project environment; no activation needed. `uv sync` without
extras installs only the core (Polars, pydantic, PyYAML) and the dev tools. pip works too:
`pip install -e ".[all]"`.

## Your own data

1. **Look before you import.** `uv run dea detect export.csv` prints the detected layout
   (long or wide), the encounter, time, variable, value and unit columns, and the time unit.
   Everything it guessed can be overridden: `--encounter-col`, `--time-col`, `--variable-col`,
   `--value-col`, `--unit-col`, `--time-unit {datetime,s,m,h,d}`, `--table-kind {long,wide}`.
2. **Import.** `uv run dea import export.csv data/mine --static patients.csv` runs the data
   quality checks first (mixed units, implausible values, duplicates, time offsets) and prints
   them, then writes the cohort. Nothing is written if the import fails. `--mapping site.yaml`
   applies your local-name-to-concept mapping on the way (see `harmonization.md`).
3. **Check and harmonize.** `uv run dea check data/mine` repeats the checks;
   `uv run dea suggest data/mine --apply` renames recognized variables to canonical names,
   moves constant attributes to the static table, sets processing defaults and adds clinical
   tags. Each suggestion can also be applied one by one in the web app.
4. **Look.** `uv run dea serve --data-dir data` and open http://127.0.0.1:8000.

What a table needs: one encounter id column, one time column (timestamps or offsets since
admission), and either `variable`/`value` columns (long) or one column per variable (wide).
Non-numeric values become events, string columns of a static table become filters.

Directories of several tables, the DEA v1 `static.csv` + `<id>/dynamic.csv` tree, and zip
uploads through the web app are detected the same way.

## MIMIC-IV

### Getting access

MIMIC-IV is hosted on PhysioNet. The **demo** (100 patients, every table) only needs a free
PhysioNet account. The **full database** needs credentialing:

1. Create an account at https://physionet.org/register/.
2. Complete the CITI course "Data or Specimens Only Research" (free, a few hours) and upload the
   completion report to your PhysioNet profile.
3. Submit the credentialing application on PhysioNet (a reference is required; a week or two).
4. Sign the data use agreement on the MIMIC-IV project page:
   https://physionet.org/content/mimiciv/.

### Download

```bash
# demo (open after login, ~15 MB)
wget -r -N -c -np --user YOU --ask-password https://physionet.org/files/mimic-iv-demo/2.2/
# full (credentialed, ~7 GB compressed)
wget -r -N -c -np --user YOU --ask-password https://physionet.org/files/mimiciv/3.1/
# clinical notes (separate credentialed project, optional)
wget -r -N -c -np --user YOU --ask-password https://physionet.org/files/mimic-iv-note/2.2/
```

Keep the directory layout as downloaded: `icu/` and `hosp/` (and `note/` if you have it)
side by side. Both `.csv` and `.csv.gz` are read.

### Import

```bash
uv run dea import physionet.org/files/mimic-iv-demo/2.2 data/mimic-demo
uv run dea import physionet.org/files/mimiciv/3.1 data/mimic --name mimic
uv run dea serve --data-dir data
```

One encounter per ICU stay (`stay_id`), time as offset from `intime`. The importer reads
`icustays`, `chartevents`, `d_items`, `patients`, `admissions`, `labevents` (attached to the
stay window), `diagnoses_icd` (ICD-9 and ICD-10 as `dx_<group>` columns), `inputevents` and
`procedureevents` (as intervals), `outputevents`, and discharge/radiology notes as events.
Units are read per row and converted to one canonical unit per variable, so `Temperature
Fahrenheit` arrives in °C and `Inspired O2 Fraction` as a fraction; the import report lists
what was converted.

For the full database the first import converts the large gzipped tables (`chartevents`,
`labevents`, `inputevents`) to Parquet parts in a cache directory (`$DEA_CACHE_DIR`, else
`.dea_cache` next to the data, else the temp directory). That takes minutes; every later
import reuses the parts. Memory stays bounded.

After the import, `uv run dea suggest data/mimic` offers the harmonization renames
(`Heart Rate` → `heart_rate`, both temperature items → `temperature`, ...) and tags.

## FHIR

A directory of FHIR R4 NDJSON files (bulk export) or JSON bundles imports directly:

```bash
uv run dea import ./fhir-export data/fhir
```

Patient, Encounter, Observation (LOINC-coded, components flattened), Condition (ICD or SNOMED)
and MedicationAdministration are read; Organizations become the site column. `uv run dea export
data/fhir out/ --fhir` writes a cohort back as FHIR. Free public test data:
`git clone --depth 1 --filter=blob:none --sparse https://github.com/smart-on-fhir/generated-sample-data`
then `git sparse-checkout set R4`.

## Several hospitals

If a static column names the hospital or site (`site`, `hospital`, `center`, `provider`, ...),
DEA picks it up; otherwise set it on the overview page. The site comparison then shows case
mix, per-site distributions, coverage and sampling grids, and flags unit, shift and coverage
differences with site-restricted fixes. `uv run dea sites data/mine` prints the same.

## To a model

```bash
uv run dea ml data/mimic ds.parquet --label "Severe ARDS" --cutoff 24 --split site
```

See `ml.md` for what the label, cutoff, split and leakage guard do.

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

If you do not want a checkout, install DEA as a tool and drop the `uv run` prefix everywhere
below:

```bash
uv tool install "dea[all] @ git+https://github.com/DeastinY/diagnostic-expert-advisor"
```

## Update

`uv run dea update` (or just `dea update` for a tool install) pulls the newest version: in a
checkout that is `git pull` followed by `uv sync --all-extras`, for a tool install it is
`uv tool upgrade dea`. `dea update --check` only reports. `dea serve --auto-update` updates
before starting, which is handy for a machine that runs DEA permanently; `DEA_AUTO_UPDATE=1`
does the same. The sidebar of the web app shows a hint when there is something new, set
`DEA_NO_UPDATE_CHECK=1` if the machine should not contact GitHub.

## Start the app

```bash
uv run dea serve --data-dir data
```

Open http://127.0.0.1:8000. The data directory holds one folder per cohort and is created if
it does not exist. Everything below can be done from this page; the command line equivalents
are listed for scripts.

## Your own data

In the browser: drop your file or folder anywhere on the cohorts page, or click the drop zone
to choose one. The guided import then goes through four steps:

1. **What we found.** Layout, columns, time unit and units; for a table you can correct the
   column mapping with a preview of the rows in front of you.
2. **Review & fix.** The import runs without writing anything and you get the counts, the
   findings and the unit conversions. Findings DEA can repair are listed under "Fix now" with
   a checkbox each: duplicates, numbers stored as text and unit conversions it is sure about
   are ticked already, dropping values or pre-admission rows is not. "Apply and check again"
   runs the import once more with the ticked fixes and shows what remains, so you can see the
   effect before anything is written. The rest is grouped under "for later" and waits on the
   profile page.
3. **Import.** Writes the cohort with the chosen fixes, each one recorded in `cohort.yaml`.
   Optionally the suggested harmonization (canonical variable names, static attributes, tags,
   processing defaults) is applied in the same go.
4. **Done.** What was imported, what was fixed, and what is left to look at.

For a table you can add a separate static table (one row per encounter with age, sex,
outcome, hospital, ...) in the "what we found" step, by path or upload. "Apply and check
again" and the import itself reuse the import that was already checked, so they take seconds,
not another full read.

A few things happen on their own at import, with no checkbox: units are converted per row to
the concept's unit (°F to °C, mEq/L to mmol/L for sodium and potassium, "units" charted on a
per-hour item to U/h); rows in a unit that cannot be converted become their own variable, for
example `WBC (#/hpf)` next to `WBC`, so one variable never mixes units; and a variable whose
rows all sit in a unit that does not fit its dictionary match (a vancomycin *level* in ug/mL
under a drug concept in mg) is left as it is and not mapped. Names that say they are something
else ("PH (dipstick)", "Urine Sodium", "Vancomycin (Trough)") are not matched in the first
place, and when most values of a matched variable fall outside the concept's plausible range
the review step offers to unmap it rather than to drop the values.

From the command line, `uv run dea import export.csv data/mine --fix recommended` applies the
same recommended fixes, and `--fix duplicates,time:negative` a list of check ids from
`uv run dea check`.

Dropped folders are uploaded in batches into `.uploads/` inside the data directory and removed
after the import. For a folder that is already on the machine and large (the full MIMIC-IV),
paste its path into the field below the drop zone instead.

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

Drop the downloaded `2.2` folder onto the cohorts page (or paste its path), or:

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

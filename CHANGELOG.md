# Changelog

## 2.0.0a2

Fixes
- The overview no longer fails on cohorts with hundreds of variables: Altair's row limit is
  off and the coverage heatmap draws the 40 best covered variables. Charts default to the
  site column (or the first small categorical column) and the most observed variable instead
  of `subject_id` and the alphabetically first variable.

Packaging
- The built web app is committed in `frontend/dist` and bundled into the wheel as `dea/static`,
  so a clone or an installed package serves the UI without node. `dea serve` warns when no
  build is found and the API's root page says how to build one; `DEA_FRONTEND_DIR` overrides.
- Dev tools live in the `dev` dependency group only; CI installs with uv.
- Project page (GitHub Pages from `docs/`) and a shorter README.

Data model
- Interval table (`intervals/`): infusions with a rate, ventilation episodes and procedures as
  `encounter_id, start, end, variable, value`; `<variable>__hours` features; synthetic episodes.
- Value fixes recorded in `cohort.yaml` (`value_fixes`), optionally restricted to one site.
- `site_column` names the hospital/site/export column of a pooled cohort.

Import
- Data quality checks at import time and on demand: mixed units, out-of-range values with the
  unit that explains them, two-cluster distributions, duplicates, time offsets, empty encounters,
  numbers stored as text; one-click fixes.
- Per-row unit normalization to UCUM with a conversion table; LOINC and UCUM codes on every
  dictionary concept; fourteen drugs with RxNorm/ATC codes.
- MIMIC-IV: diagnoses (ICD-9/ICD-10 groups), inputevents, procedureevents, outputevents,
  °F/°C items, block-wise conversion of large gzipped tables.
- FHIR R4 import and export (Patient, Encounter, Observation, Condition,
  MedicationAdministration), transaction bundles, organizations as sites.
- Site mapping files; bulk concept mapping through the advisor.

Analysis
- Site comparison: per-site profiles, normalized histograms on shared bins, coverage and
  sampling matrices, case mix, and findings for unit, shift, coverage and sampling differences.
- Selection-versus-rest overlay on the overview; cohort band behind patient series.
- `dea.ml.make_dataset`: label, cutoff, split (encounter, site, group), leakage guard.

App
- Landing page redesign, import preview with checks, persisted import report, result cache,
  optional bearer-token protection, richer HTML report.

## 2.0.0a1

- Clean-slate v2: Polars long data model with hash-bucket Parquet storage, importers (long,
  wide, v1 CSV tree, MIMIC-IV), profiler, offline suggestions, OpenAI-compatible advisor,
  local NLP connectors, compute backends (serial, local, SLURM), FastAPI + React web app,
  Altair chart layer, HTML report.

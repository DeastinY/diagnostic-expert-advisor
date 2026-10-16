# Changelog

## 2.0.0a2

Web app
- The profile page has a Findings card with the same prioritized list as the import review:
  tick, apply, done; the suggestions card keeps the rest. "Show the data" under a finding
  draws the variable's histogram with the plausible range shaded, before and after import.
- Previews and imports run as background jobs; the wizard shows the stage ("reading
  icu/chartevents", "converting chartevents.csv.gz, part 3", "checks: plausible ranges") and
  the elapsed time. The CLI and the Python API see the same stages through
  `dea.io.progress`.
- The review remembers the last import of a cohort with the same name: what was applied and
  what was declined is pre-ticked the same way, with the previous counts for comparison.
- The review step leads with a one-line verdict, lists the fixes biggest first (severity, then
  share of rows) with the five largest shown and the rest behind "show smaller ones", and
  folds everything without a fix into groups with a preview of the variables.
- "Apply and check again" and the final import reuse the checked import kept in memory, so
  they only apply the fixes instead of reading the source again. A table import can take a
  separate static table (path or upload) in the "what we found" step.

- Review & fix step in the guided import: counts, a severity bar, the fixes DEA can apply
  grouped with a checkbox each (recommended ones ticked), "apply and check again" to see what
  remains before anything is written, the unit conversions in a table and the rest grouped
  for later. Import applies the chosen fixes; the CLI does the same with `dea import --fix`.
- Fixes for numbers stored as text (the leading number becomes an observation) and for rows
  before admission; rarely recorded variables can be hidden in one go.

Harmonization
- Names that denote another specimen or a drug level ("PH (dipstick)", "Urine Sodium",
  "Vancomycin (Trough)", "FiO2 (set)") are no longer matched to the concept of the plain
  name. When most values of a matched variable sit outside the concept's plausible range and
  no unit explains it, the check proposes to unmap the variable (data kept, concept, code and
  range forgotten; remembered in `cohort.yaml` as `unmapped`) instead of dropping the values.
- Findings are reported once per id.

Units
- Rows in a unit that cannot be converted become their own `<variable> (<unit>)` variable
  instead of staying mixed with converted rows; a variable whose rows all sit in a unit that
  does not fit its dictionary match is left alone and not mapped (a drug level is not a dose).
- `units` is no longer read as pH; mEq/L converts to mmol/L for sodium and potassium; "units"
  on a per-hour heparin or insulin item is U/h; ug/mL, mg and U/h have aliases.
- Guided import: drop a folder or a file anywhere on the cohorts page (a whole MIMIC-IV or
  FHIR download works), see what was recognized, run the checks, import, and optionally apply
  the suggested harmonization in one go. Folder uploads go in batches to `.uploads/` in the
  data directory and are removed after the import. Pasting a path still works for large
  folders that are already on the machine.

Fixes
- The overview no longer fails on cohorts with hundreds of variables: Altair's row limit is
  off and the coverage heatmap draws the 40 best covered variables. Charts default to the
  site column (or the first small categorical column) and the most observed variable instead
  of `subject_id` and the alphabetically first variable.

Project page
- Redesigned: scroll progress and active nav, a playing import log in the hero, staggered
  reveals, card tilt, a sliding tab indicator, a standards marquee, plainer copy throughout,
  and a contact address in the workshops section and footer.
- Pages deploys a placeholder (`docs/public`) until the page is finalized; the full landing
  page stays in `docs/` and goes live by pointing the workflow back at it.
- Workshops & support section: regular workshops, personal demonstrations and setup help
  with the Simulation and Data Lab Digital Patient (SDL DP) of NHR4CES; screenshots of the
  review step with a histogram and of the profile page with its findings.

Packaging
- `dea update` (git pull + uv sync in a checkout, `uv tool upgrade` for a tool install),
  `dea update --check`, `dea version`, `dea serve --auto-update` / `DEA_AUTO_UPDATE=1`, and an
  update hint in the sidebar (`DEA_NO_UPDATE_CHECK=1` turns the GitHub check off).
- Installable without a clone: `uv tool install "dea[all] @ git+<repo>"`.
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

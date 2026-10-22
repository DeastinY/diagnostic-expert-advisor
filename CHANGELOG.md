# Changelog

## Unreleased

Import: a folder with one file per patient
- A folder of tables with the same columns, one per patient or stay, is recognized as such:
  the per-encounter files are found by their headers (order and a few columns may differ),
  the encounter id is what differs between the file names, and a static table is the other
  table with a column that names those ids. The "what we found" step shows the pattern, the
  static table and its id column, the time column and unit, and what in the folder was
  ignored; all of it can be changed before the checks run.
- The manual route when nothing is recognized: name the files with a pattern
  (`patient_*.csv`) and pick the static table, in the app or with `dea import --pattern`
  and `--static-file`; or read one of the folder's tables on its own.
- Semicolons and decimal commas are read as such. A thousand small files import in seconds:
  they are read with one text schema and parsed once, stacked on the union of their columns.
- Columns that are empty in every file are reported as such and left out, not kept as events.
- German column names for the encounter id (Pseudo-ID, Fallnummer) and the time (Zeit,
  Uhrzeit, Zeitpunkt) are recognized.

Data before admission
- The run-up that still belongs to a stay is a cohort setting, `processing.pre_admission_lead_hours`
  (24 by default, editable under Processing). The checks use it: records further back are
  flagged with a drop fix; the run-up itself can be left alone, dropped, or counted as
  admission values (`Cohort.clamp_to_admission` moves it to hour 0).
- Every encounter carries `pre_admission_hours` and `n_pre_admission` in its features, so
  rules can select stays with a long run-up; the patient page shades the time before
  admission; a "Before admission" overview panel shows how much data precedes admission and
  how far back it reaches, and is on by default when there is any.
- A cached feature table from an older DEA is recomputed when it lacks newer columns; the
  patient page no longer fails when a variable is listed twice for a panel.

Rename suggestions show the standard they land on
- Each rename on the profile page reads local name → canonical name, with the concept's label
  and unit, its LOINC and UCUM codes (RxNorm and ATC for drugs) as chips, and the other local
  names that become the same variable, so "Admission Weight (Kg)" and "Admission Weight
  (lbs.)" both going to `weight` is stated instead of looking like a mistake. The API carries
  this as a `concept` field on harmonize suggestions.
- A medication match is dropped when the variable's recorded unit cannot be converted into
  the concept's unit at all (a level in ug/mL, a bolus amount in mg against an infusion rate,
  "dose" under a heparin name), not only when it is a concentration. mcg/h, mg/h and mg/kg/h
  got their aliases, and ug/kg/min converts into mg/kg/h, so rates in other spellings still
  match.
- Project page rewritten: six features with app screenshots, an example import walkthrough,
  uv tool quick start, a Python research section, the new icon; all screenshots dark.

Overview as a dashboard
- The overview is a four-column grid of plots the user composes: "Add plot" opens a catalogue
  with search over kinds, variables and static columns; every panel has its parameters inline,
  four widths, drag or arrow reordering, a rename on double-click and a remove. The layout is
  saved with the cohort; "Reset to default" brings DEA's back.
- DEA's default: the numbers, length of stay next to tags, the coverage heatmap, age, sex,
  outcome by sex, outcome per site, and the trajectory of the main variable. Trajectories,
  per-site distributions and coverage, variable distributions, the most observed variables
  and the selection-versus-rest comparison are in the catalogue instead of on the page.
- A light/dark toggle sits top right and sticks; charts redraw in the chosen theme.
- Categorical bars (tags, static attributes, most observed) use the whole series palette;
  crosstabs stack one attribute by another as counts or shares.
- Export Parquet, export FHIR and the report moved from the overview to Build cohort.
- Right after an import, the cohorts page shows the new cohort in numbers: stays, most
  observed variables, age, sex, outcome, unit, the distributions of the 24 most observed
  variables and the coverage heatmap, with buttons to the overview and the findings.
- Trajectories start at admission; a site column has to be categorical to be used as one.

Overview
- The length-of-stay histogram counts whole days, the tags card shows the eight most frequent
  tags so it stays the height of the histogram, the KPI tiles spread across the card, and the
  coverage heatmap has a day axis and its legend on top.
- Cached chart results are keyed on the code as well as the cohort's files, so a chart change
  shows on an existing cohort without re-importing it.

Review step: what a fix does, with the data
- Duplicates are two findings: exact repeats (same value, safe to drop, listed by variable)
  and conflicting values at the same timestamp (two lab runs, a correction), which are listed
  with their values and resolved only by an explicit choice: keep the first, keep the last,
  keep the mean, or drop them all.
- "Numbers stored as text" quotes the real values with their row counts, and says which rows
  stay events.
- A unit conversion shows example values before and after, how many rows already fit and
  stay as they are, and "show before and after" draws both distributions.
- Values out of range offer drop or clamp, and up to 25 of them can be decided one by one,
  each with the encounter's series plotted around the flagged value.
- A choice travels with the fix id (`range:MAP:above#clamp`, `...#rows=...`), so the CLI, the
  API and the remembered choices of a re-import all understand it.

First run on real data: the MIMIC-IV demo (100 patients, 140 ICU stays, 306k observations)
imports in under a minute, on the command line and through the guided import.

MIMIC-IV importer
- Lab items are named by label and specimen: urine pH no longer lands in the blood pH
  variable, urine WBC (per field) is its own variable instead of a split-off unit.
- pH recorded with the unit "units" is accepted as a pH instead of being split off.
- Item labels are tidied ("Side  Rails" and "Side Rails" were two variables).

Harmonization
- Name matching keeps systolic, diastolic and mean apart: "Non Invasive Blood Pressure
  systolic" matched the diastolic concept at 93%.

Web app and charts
- The coverage heatmap starts at admission; a chart row dated a year earlier no longer
  stretches the axis to minus 9,000 hours.
- The interventions and events rails on a patient page show the twelve busiest kinds and say
  how many there are in all; a MIMIC stay has over a hundred.
- The profile lists constant and static-as-time-series variables as one line per kind once
  there are more than five, instead of hundreds of warnings.
- The overview tile counts event types and says so; the cohort list refreshes when an import
  finishes.

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

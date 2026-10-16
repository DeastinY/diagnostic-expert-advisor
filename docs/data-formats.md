# Importing data

`dea.io.detect(path)` guesses the layout and the key columns; `dea.io.import_path(path,
**overrides)` applies it. The UI exposes the same guesses as editable dropdowns.

## Long table

One row per observation. Typical for MIMIC-style exports.

| stay_id | charttime           | itemid | valuenum |
|---------|---------------------|--------|----------|
| 1042    | 2024-03-01 08:00:00 | HR     | 82       |

Detected columns: an id-like column (`encounter`, `stay`, `hadm`, `patient`, `subject`,
`*_id`), a time column (datetime dtype or `time`, `charttime`, `offset`, `minutes`, …), a
variable column (`variable`, `item`, `label`, `parameter`, …) and a value column (`value`,
`valuenum`, `result`). Times may be absolute timestamps (converted to time since the
encounter's first observation) or numeric offsets in `s`, `ms`, `m`, `h` or `d`.

```bash
dea import events.csv data/my_cohort --encounter-col stay_id --time-col charttime \
    --variable-col itemid --value-col valuenum --time-unit datetime
```

## Numbers, strings and timestamps

Numeric columns (and numeric-looking strings) become **observations**. Other string columns
become **events** with the same shape (`encounter_id, time, variable, value`), so ventilation
modes, drug names or free-text flags are kept rather than dropped; the profiler lists them and
flags high-cardinality ones that may contain identifying text. Columns of other types are
dropped and reported.

When the time column holds absolute timestamps, `time` becomes the offset from the encounter's
first observation and the first timestamp is kept as an `admission_time` static column, so
nothing is lost. Imports are lazy end to end: the source is scanned, not read, and streams to
Parquet on save, so files larger than memory are fine.

## Wide table

One row per encounter and time, one column per variable. Non-numeric columns are
dropped and reported. Columns that are constant within an encounter (age, sex) can be
moved to the static table with `static_cols=[...]`.

## DEA v1 CSV tree

```
cohort/
  static.csv          first (unnamed) column = encounter id
  <id>/dynamic.csv    first (unnamed) column = minutes since admission
```

```bash
dea import /path/to/v1/cohort data/migrated --time-unit m
```

## Separate static table

Any table import accepts `--static static.csv` (CLI) or `static_path=` (Python) with an
`encounter_id` column (or any id-like column name).

## Programmatic use

```python
import polars as pl
from dea.io import import_long, import_wide

cohort, report = import_long(df, "stay_id", "charttime", "itemid", "valuenum")
cohort, report = import_wide(df, "patient", "hours", time_unit="h", static_cols=["age", "sex"])
print(report.summary_lines())
cohort.save("data/my_cohort")
```

## FHIR R4 and coding standards

FHIR bulk exports (NDJSON) and Bundles import directly, and any cohort exports as FHIR
resources with LOINC, UCUM and ICD codes. Units are normalized per row during import, and
ICD-9/ICD-10 diagnoses become `dx_<group>` static columns. See [harmonization.md](harmonization.md).

## Intervals

Interventions that last (infusions with a rate, ventilation episodes, procedures) live in a
third long table, `intervals/`, with columns `encounter_id, start, end, variable, value`;
`start` and `end` are offsets from admission like `time`. The feature table carries one
`<variable>__hours` column per interval variable (total duration in the stay), so tags such
as `"Ventilation__hours" > 0` work like any other rule; a rule naming an intervention nobody
received evaluates as zero hours. MIMIC `inputevents` (value = rate) and `procedureevents`
feed it, `outputevents` become observations, and the encounter view draws the spans as a rail
above the time series.

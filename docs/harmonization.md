# Checks and harmonization

Hospital data arrives in the units, codes and vocabularies of the system it came from. DEA
runs a fixed set of checks on every import and harmonizes measurements, units and diagnoses
onto three standards: **LOINC** and **UCUM** for what was measured and in which unit,
**ICD-9-CM / ICD-10** for diagnoses, and **FHIR R4** as the exchange format in both
directions. All of it works offline; the AI advisor is optional on top.

## Checks

`dea check <cohort>`, `GET /api/cohorts/{name}/checks`, the "Check first" button on the
import form and `ImportReport.checks` all run `dea.quality.run_checks`. Every finding has a
severity, the affected variables and, where DEA can repair it, an action that the profile
page applies with one click. Actions are recorded in `cohort.yaml` under `value_fixes`, so the
provenance of every stored value stays visible.

| Check | Finds | Severity | Action |
|---|---|---|---|
| units | rows of a known variable recorded in a different unit than the rest (°F next to °C, % next to a fraction, mmol/L next to mg/dL) | warning / error when no conversion exists | converted at import |
| range | values outside the plausible range of a dictionary concept; when a known conversion moves the median outlier into range the finding names the unit (e.g. "1.2% of 'Temp' look like [degF]") | warning | convert, or drop the implausible rows |
| cluster | two separated value clusters in a variable the dictionary does not know (a unit mix, two devices, two measurements under one name) | warning | none, rename or split manually |
| duplicates | the same encounter, time and variable recorded more than once | warning | keep the first |
| time | observations before admission, or more than 90 days after it | warning | none |
| encounters | encounters without any observation | warning | filter "Has observations" |
| static | columns missing for more than 30% of encounters, ages above 120 | info / warning | none |
| events | string variables that are really numbers with a unit glued on ("37.2 C") | warning | clean the source |
| coverage | variables present in fewer than 10% of encounters | info | none |

Checks run as one streaming pass over the observations plus a sample of encounters, so
they cost a few seconds on MIMIC-sized cohorts. A finding backed by fewer than five rows is
never reported.

The range check deliberately uses *hard* physiological limits (a temperature of 45 °C, a
heart rate of 300), not reference ranges. Its purpose is to catch unit and mapping errors, not
to flag sick patients.

## Units: UCUM

`src/dea/suggest/units.yaml` maps the unit strings hospitals actually write (`mmHg`, `°F`,
`K/uL`, `mcg/kg/min`) onto UCUM codes and lists linear conversions between them. Analyte
specific conversions (glucose mmol/L to mg/dL) carry the concept they apply to. Every
dictionary concept has a canonical UCUM unit; importers with a per-row unit column (MIMIC's
`valueuom`, FHIR's `valueQuantity.code`, any long table with a unit column) convert matching
variables to that unit lazily and report what they changed. Variables the dictionary does not
know keep their majority unit as display unit and are left alone.

## Measurements: LOINC

Every concept in `src/dea/suggest/dictionary.yaml` carries its LOINC code. This gives three
things for free:

- A variable named by its LOINC code (`8867-4`) matches the concept directly.
- Imports record the code per variable in `variable_codes`, and the FHIR export writes it as
  `Observation.code.coding`.
- Two local names that resolve to the same concept (`Temperature Fahrenheit` and
  `Temperature Celsius` in MIMIC) are offered as one harmonization rename, and because the
  unit layer already converted the °F rows, merging them is safe.

The full LOINC table (around 100,000 codes) is licensed by Regenstrief and not bundled. The
dictionary covers the concepts common ICU and ward cohorts use; adding a concept is one YAML
entry. For labs beyond it, the FHIR import keeps the LOINC code as the variable name so
nothing is lost, and the AI advisor can propose canonical names for the rest.

## Diagnoses: ICD-9 and ICD-10

Cohorts routinely mix both versions (MIMIC-IV does within one table). An exact
code-to-code crosswalk (the CMS General Equivalence Mappings) is many-to-many and rarely what
a study needs. What research cohorts need is a stable group per encounter that holds across
versions, so `dea.standards.icd` provides:

- `parse_icd(code, version=None)`: normalizes a code, infers the version when it is missing
  (letter-first is ICD-10, numeric or `V`/`E` supplementary is ICD-9), and assigns the chapter.
- `DIAGNOSIS_GROUPS`: twenty-one clinical groups (sepsis, septic shock, ARDS, pneumonia,
  COVID-19, COPD, asthma, heart failure, myocardial infarction, atrial fibrillation, stroke,
  hypertension, diabetes, chronic and acute kidney disease, liver disease, cancer, obesity,
  delirium, dementia, trauma) defined by code prefixes in both versions.
- `diagnosis_table(frame)`: one static row per encounter with `icd_codes` (dotted codes,
  both versions, joined by `|`), `n_diagnoses`, `primary_diagnosis`, `primary_chapter` and one
  boolean `dx_<group>` column each. These are ordinary static columns, so
  `dx_sepsis = true` works as a filter, shows up in the filter suggestions and in the
  cohort builder.

The MIMIC importer reads `hosp/diagnoses_icd` and `hosp/d_icd_diagnoses`; the FHIR importer
reads `Condition` resources coded in ICD-10, ICD-10-CM, ICD-10-GM or ICD-9-CM. Groups are
prefix lists in one dictionary, so a site can extend them without touching code.

## Exchange: FHIR R4

`dea.standards.fhir` reads bulk-data exports (one NDJSON file per resource type) and Bundle
JSON files. `Patient` gives sex and age at admission, `Encounter` gives the stay and its
start, `Observation` gives measurements (LOINC coded → dictionary concept, `valueQuantity`
→ UCUM-normalized observation, `valueString` / `valueCodeableConcept` → event, `component`
panels flattened), `Condition` gives diagnoses. Observations without an encounter reference
are attached by patient and time window. Other resource types are counted and ignored.

`dea export <cohort> <dir> --fhir` (and `GET /api/cohorts/{name}/export/fhir`) writes the
inverse: `Patient`, `Encounter`, `Observation` and `Condition` NDJSON with LOINC, UCUM and
ICD systems where known, so a DEA cohort can be handed to any FHIR server or validator. A
round trip MIMIC → DEA → FHIR → DEA preserves values, offsets, units, codes and diagnosis
groups (see `tests/test_standards.py`).

Reading uses Python's JSON parser line by line: fine for exports of a few million resources,
not for a national registry. SNOMED CT, RxNorm/ATC for medications and the OMOP common data
model are the natural next steps; the dictionary already reserves `rxnorm`/`atc` fields for
drugs, and an OMOP reader would map `measurement` and `condition_occurrence` through the same
two modules.

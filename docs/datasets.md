# Public datasets to test with

DEA is developed against synthetic data (`dea synth`, with and without the MIMIC-IV layout and
with several hospitals), so every feature has a reproducible fixture. Real-world shaped data
exercises the importers differently: more resource types, more sites, sparser coverage,
names with odd characters. This page lists what is openly available, what it tests, and how
to get it.

| Dataset | Access | Shape | What it exercises |
|---|---|---|---|
| **SMART on FHIR sample data** (`smart-on-fhir/generated-sample-data`, `R4/SYNTHEA`) | public Git repository, 631 Synthea patient bundles, 766 MB | FHIR R4 transaction bundles: Patient, Encounter, Observation (LOINC + UCUM), Condition (SNOMED), Organization, Claims and more | the FHIR reader on data DEA did not write: `urn:uuid` references, 20+ organizations as sites, LOINC codes outside the dictionary, SNOMED conditions, outpatient encounters without observations |
| **MIMIC-IV Clinical Database Demo** (PhysioNet, 100 patients, open) | free, PhysioNet account | MIMIC-IV tables (`icu/`, `hosp/`) as CSV.gz | the MIMIC importer, °F/°C temperature items, per-row units, ICD-9/ICD-10 diagnoses |
| **MIMIC-IV / MIMIC-III** (PhysioNet, credentialed) | CITI training + data use agreement | full ICU database, 70k+ stays | scale: streaming import, hash-bucket storage, SLURM backends |
| **eICU Collaborative Research Database** and its demo (PhysioNet) | demo open; full credentialed | 200k stays from 208 US hospitals, `hospitalid` per stay | multi-site checks on real inter-hospital variation (units, sampling, coverage) |
| **HiRID** (PhysioNet, credentialed) | credentialed | 33k Swiss ICU stays, 2-minute resolution, long format with `variableid` | long-table import with a unit column at high sampling rates |
| **AmsterdamUMCdb** | access request | 23k Dutch ICU admissions, long format | European units (mmol/L, kPa) through the unit layer |
| **PhysioNet/CinC Challenge 2012** | open | 4,000 ICU stays, 48 h, one text file per stay, 35 variables | small long-table files, hours:minutes time stamps |
| **PhysioNet/CinC Challenge 2019 (sepsis)** | open | 40k stays, one pipe-separated file per stay, hourly wide rows | wide-table import at hourly resolution |

## SMART on FHIR sample data

The only set above that needs no account and no download page, so it is the one used in
development here (`physionet.org` was not reachable from the development sandbox).

```sh
git clone --depth 1 --filter=blob:none --sparse https://github.com/smart-on-fhir/generated-sample-data smart
cd smart && git sparse-checkout set R4
mkdir ../smart25 && ls R4/SYNTHEA/*.json | head -25 | xargs -I{} cp {} ../smart25/
dea import ../smart25 data/smart25 --name smart25
dea sites data/smart25
```

What 25 bundles give: 686 encounters (mostly ambulatory, 393 without observations), 71
numeric variables and 16 string-valued ones, 31 organizations as sites. The import takes
under three seconds. Things learned from it that changed the code:

- Transaction bundles reference resources as `urn:uuid:…` and the entry's `fullUrl` is the
  only id; both are handled now.
- LOINC display names can contain zero-width spaces (`Erythrocytes [#/​volume]`), which
  produced two variables that looked identical; names are cleaned.
- With thirty sites, one finding per site and variable is noise; site findings are now one
  per variable (which sites deviate, by how much), and a unit hypothesis has to map the whole
  quartile range of a site onto the reference, not only its median, so a paediatric site is no
  longer read as "inches".
- Weight and height plausibility ranges must include newborns.

The full 631 bundles import in about a minute; `dea sites` then compares 70+ organizations.

## PhysioNet data

All PhysioNet sets share the same route: create an account at https://physionet.org, complete
the credentialing where required, then download with `wget -r -N -c -np --user <you> --ask-password <url>`.
MIMIC-IV, its demo and eICU drop straight into `dea import <dir>` (the MIMIC layout is
detected; eICU is a set of long tables, use `--table-kind long` with `--unit-col` where
present). The 2012 and 2019 challenge sets need a few lines of concatenation into one long
or wide table first; `dea detect` then guesses the columns.

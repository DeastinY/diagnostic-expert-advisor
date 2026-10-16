# From a cohort to a model

`dea ml`, `GET /api/cohorts/{name}/ml` and `dea.ml.make_dataset` turn a cohort into one
table with one row per encounter, a boolean label and a train/test split, and take care of
the three things that go wrong when this is done by hand.

```python
from dea.io import load_cohort
from dea.ml import make_dataset

ds = make_dataset(load_cohort("data/icu"), label="Severe ARDS", cutoff_hours=24, split="site")
X_train, y_train = ds.train()          # Polars frame and series
X_test, y_test = ds.test()
ds.to_pandas()                         # or everything in one pandas frame
```

**Label.** A configured tag (`Severe ARDS`), a static condition (`outcome = 'deceased'`) or any
SQL rule over the feature table. It is evaluated on the whole stay.

**Cutoff.** With `cutoff_hours`, features are computed from observations and interventions
before that time only: aggregates (`<variable>__min|max|mean|first|last|count`), intervention
hours clipped at the cutoff, static columns. Nothing after the cutoff reaches the model.

**Leakage guard.** Columns that encode the outcome (`outcome`, `mortality`, `dod`,
`discharge_location`, ...), the length of stay when a cutoff is set, and the columns the label
itself references are dropped; the dataset lists what it removed. Pass `exclude=[...]` for
site-specific ones.

**Split.** `encounter` (seeded shuffle), `site` (whole sites held out: the only honest estimate
for a model meant to travel between hospitals) or `group:<column>` (for example
`group:patient_id`, so a patient's stays never straddle the split).

**Encoding.** Booleans become 0/1, categorical static columns with at most 20 levels are
one-hot encoded, other text columns are dropped. `encode=False` keeps the raw frame.

The feature table is computed with Polars' streaming engine and cached per cohort, so a
MIMIC-sized dataset is a few seconds. For per-encounter feature engineering beyond the built-in
aggregates, `Cohort.apply` runs any function over encounters on the local or SLURM backend
(`docs/hpc.md`) and stores the result under `derived/`, from where it joins the dataset.

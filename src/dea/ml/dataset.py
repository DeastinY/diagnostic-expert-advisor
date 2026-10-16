"""Build an encounter-level (X, y) with the three things researchers get wrong by hand.

* **Label**: a configured tag, a static column condition or any SQL rule, evaluated on the
  *whole* stay.
* **Cutoff**: features are computed only from observations and interventions before
  ``cutoff_hours`` after admission, so an early-prediction model never sees the future.
  Columns that encode the outcome or the length of stay are dropped.
* **Split**: by encounter (seeded shuffle), by site (whole sites held out, which is the only
  honest estimate for a multi-center model) or by any grouping column such as ``patient_id``.

The result is a Polars frame; ``to_pandas()`` and ``to_numpy()`` hand it to any library.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from dea.core.model import ENCOUNTER_ID, END, START, TIME, Cohort, FilterError

LEAKY_STATIC = (
    "outcome",
    "mortality",
    "died",
    "death",
    "deathtime",
    "dod",
    "hospital_expire_flag",
    "discharge_location",
    "discharge_disposition",
    "los",
    "icu_los_days",
    "los_days",
)
MAX_CATEGORIES = 20
_QUOTED = re.compile(r'"([^"]+)"')
_IDENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")


@dataclass
class DatasetSpec:
    label: str
    cutoff_hours: float | None = None
    split: str = "encounter"  # encounter | site | group:<column>
    test_size: float = 0.2
    seed: int = 0
    features: list[str] | None = None  # variables to aggregate; default: all
    exclude: list[str] = field(default_factory=list)  # extra static columns to drop
    encode: bool = True

    def describe(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class Dataset:
    X: pl.DataFrame  # encounter_id + features
    y: pl.Series  # boolean label aligned with X
    split: pl.Series  # "train" / "test" aligned with X
    groups: pl.Series | None
    spec: DatasetSpec
    dropped: list[str]  # columns removed to avoid leakage

    @property
    def feature_names(self) -> list[str]:
        return [c for c in self.X.columns if c != ENCOUNTER_ID]

    def frame(self) -> pl.DataFrame:
        """X, y and split in one table (what the CLI and API export)."""
        return self.X.with_columns(self.y.alias("y"), self.split.alias("split"))

    def part(self, which: str) -> tuple[pl.DataFrame, pl.Series]:
        mask = self.split == which
        return self.X.filter(mask).drop(ENCOUNTER_ID), self.y.filter(mask)

    def train(self) -> tuple[pl.DataFrame, pl.Series]:
        return self.part("train")

    def test(self) -> tuple[pl.DataFrame, pl.Series]:
        return self.part("test")

    def to_pandas(self):  # noqa: ANN201 - pandas is optional
        return self.frame().to_pandas().set_index(ENCOUNTER_ID)

    def to_numpy(self, which: str = "train"):  # noqa: ANN201
        X, y = self.part(which)
        return X.to_numpy(), y.to_numpy()

    def summary(self) -> dict[str, Any]:
        return {
            "n": self.X.height,
            "n_features": len(self.feature_names),
            "positives": int(self.y.sum()),
            "train": int((self.split == "train").sum()),
            "test": int((self.split == "test").sum()),
            "dropped": self.dropped,
            "spec": self.spec.describe(),
        }


def _label_expr(cohort: Cohort, label: str) -> str:
    for rule in cohort.config.tags + cohort.config.filters:
        if rule.label == label:
            return rule.where
    return label  # a static column condition or any SQL rule over the feature table


def _truncated(cohort: Cohort, cutoff_hours: float) -> Cohort:
    """The cohort as it looked ``cutoff_hours`` after admission."""
    cut = pl.duration(seconds=int(cutoff_hours * 3600))
    return Cohort(
        cohort.static,
        cohort.dynamic.filter(pl.col(TIME) <= cut),
        events=cohort.events.filter(pl.col(TIME) <= cut),
        intervals=cohort.intervals.filter(pl.col(START) < cut).with_columns(
            pl.when(pl.col(END) > cut).then(cut).otherwise(pl.col(END)).alias(END)
        ),
        config=cohort.config,
        _full=False,
    )


def _encode(frame: pl.DataFrame) -> tuple[pl.DataFrame, list[str]]:
    """Booleans to 0/1, small categoricals one-hot, everything else non-numeric dropped."""
    dropped = []
    out = frame
    for name, dtype in frame.schema.items():
        if name == ENCOUNTER_ID or dtype.is_numeric():
            continue
        if dtype == pl.Boolean:
            out = out.with_columns(pl.col(name).cast(pl.Int8()))
        elif dtype in (pl.String, pl.Categorical, pl.Enum):
            levels = frame.get_column(name).drop_nulls().unique().sort().to_list()
            if 1 < len(levels) <= MAX_CATEGORIES:
                out = out.with_columns(
                    [(pl.col(name) == lvl).cast(pl.Int8()).alias(f"{name}={lvl}") for lvl in levels]
                ).drop(name)
            else:
                out = out.drop(name)
                dropped.append(name)
        else:
            out = out.drop(name)
            dropped.append(name)
    return out, dropped


def _split(
    X: pl.DataFrame, spec: DatasetSpec, site_col: str | None
) -> tuple[pl.Series, pl.Series | None]:
    rng = random.Random(spec.seed)
    ids = X.get_column(ENCOUNTER_ID).to_list()
    if spec.split == "encounter":
        order = ids[:]
        rng.shuffle(order)
        test = set(order[: max(1, int(round(len(order) * spec.test_size)))])
        return pl.Series("split", ["test" if i in test else "train" for i in ids]), None
    column = site_col if spec.split == "site" else spec.split.split(":", 1)[1]
    if not column or column not in X.columns:
        raise FilterError(f"split column {column!r} not in the cohort")
    groups = X.get_column(column).cast(pl.String()).fill_null("?")
    sizes = groups.value_counts().sort("count", descending=True)
    # hold out whole groups, smallest first, until the test share is reached
    held, n_test, total = set(), 0, X.height
    for g, n in reversed(sizes.rows()):
        if n_test >= spec.test_size * total:
            break
        held.add(g)
        n_test += n
    if len(held) == len(sizes):  # a single group: fall back to a random split within it
        return _split(X, DatasetSpec(**{**spec.__dict__, "split": "encounter"}), site_col)
    split = pl.Series("split", ["test" if g in held else "train" for g in groups.to_list()])
    return split, groups.alias("group")


def make_dataset(cohort: Cohort, spec: DatasetSpec | None = None, **kwargs: Any) -> Dataset:
    """Encounter-level features and a boolean label, split, with the leakage guard applied."""
    spec = spec or DatasetSpec(**kwargs)
    where = _label_expr(cohort, spec.label)
    y_full = cohort.features().select(ENCOUNTER_ID, cohort.evaluate(where).alias("y"))
    source = _truncated(cohort, spec.cutoff_hours) if spec.cutoff_hours else cohort
    if spec.features is not None:
        source.config = source.config.model_copy(update={"feature_variables": list(spec.features)})
    feats = source.features().join(y_full, on=ENCOUNTER_ID, how="left")
    y = feats.get_column("y").fill_null(False)
    feats = feats.drop("y")
    site_col = cohort.config.site_column
    group_col = (
        site_col
        if spec.split == "site"
        else (spec.split.split(":", 1)[1] if ":" in spec.split else None)
    )
    dropped = []
    for c in list(feats.columns):
        lower = c.lower()
        leaky = lower in LEAKY_STATIC or lower.startswith(("outcome", "death")) or c in spec.exclude
        if spec.cutoff_hours and c == "los_hours":
            leaky = True
        if leaky and c != group_col:
            feats = feats.drop(c)
            dropped.append(c)
    # the label's own columns (a static condition such as outcome = 'deceased') never stay
    referenced = set(_QUOTED.findall(where)) | set(_IDENT.findall(where))
    for c in feats.columns:
        if c != ENCOUNTER_ID and c != group_col and c in referenced:
            feats = feats.drop(c)
            dropped.append(c)
    split, groups = _split(feats, spec, site_col)
    if group_col and group_col in feats.columns and spec.split != "encounter":
        feats = feats.drop(group_col)
        dropped.append(group_col)
    X, more = _encode(feats) if spec.encode else (feats, [])
    return Dataset(X=X, y=y, split=split, groups=groups, spec=spec, dropped=dropped + more)


__all__ = ["Dataset", "DatasetSpec", "make_dataset"]

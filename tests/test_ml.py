from __future__ import annotations

from pathlib import Path

import polars as pl

from dea.ml import DatasetSpec, make_dataset
from dea.synth import generate_cohort


def test_dataset_label_cutoff_and_split() -> None:
    cohort = generate_cohort(40, seed=11, max_hours=96)
    ds = make_dataset(cohort, label="Severe ARDS", cutoff_hours=24, test_size=0.25, seed=1)
    assert ds.X.height == 40 and ds.y.dtype == pl.Boolean
    assert ds.y.to_list() == cohort.tag_matrix()["Severe ARDS"].to_list()  # whole-stay label
    assert "Horowitz__min" in ds.dropped  # the label's own column never stays
    assert "los_hours" in ds.dropped and "outcome" in ds.dropped
    # no feature sees anything after the cutoff
    full = cohort.features()
    assert (ds.X["HR__count"] <= full["HR__count"]).all()
    assert ds.X["HR__count"].max() <= 24 * 60 + 1
    assert set(ds.split.unique()) == {"train", "test"} and (ds.split == "test").sum() == 10
    assert all(not isinstance(ds.X.schema[c], pl.String) for c in ds.feature_names)
    assert any(c.startswith("sex=") for c in ds.feature_names)  # one-hot categoricals
    Xtr, ytr = ds.train()
    assert Xtr.height == ytr.len() == 30 and "encounter_id" not in Xtr.columns
    assert ds.summary()["positives"] == int(ds.y.sum())


def test_site_split_holds_out_whole_sites() -> None:
    cohort = generate_cohort(30, seed=3, max_hours=48, sites=3)
    ds = make_dataset(cohort, DatasetSpec(label="Vasopressors", split="site", test_size=0.3))
    assert ds.groups is not None
    test_sites = set(ds.groups.filter(ds.split == "test").unique())
    train_sites = set(ds.groups.filter(ds.split == "train").unique())
    assert test_sites and not (test_sites & train_sites)
    assert "site" in ds.dropped and not any(c.startswith("site=") for c in ds.feature_names)


def test_static_condition_label_and_cli(tmp_path: Path) -> None:
    from dea.cli import main
    from dea.synth import write_synthetic

    write_synthetic(tmp_path / "c", n_encounters=12, seed=2, max_hours=24)
    out = tmp_path / "ds.parquet"
    assert (
        main(
            [
                "ml",
                str(tmp_path / "c"),
                str(out),
                "--label",
                "outcome = 'deceased'",
                "--cutoff",
                "12",
            ]
        )
        == 0
    )
    frame = pl.read_parquet(out)
    assert {"y", "split", "encounter_id"} <= set(frame.columns) and "outcome" not in frame.columns
    assert frame.height == 12

from __future__ import annotations

from pathlib import Path

import polars as pl

from dea.core import ENCOUNTER_ID, Cohort
from dea.io import is_cohort_dir, list_cohorts, load_cohort, save_cohort


def test_layout(cohort_dir: Path) -> None:
    assert is_cohort_dir(cohort_dir)
    assert (cohort_dir / "cohort.yaml").exists()
    assert (cohort_dir / "static.parquet").exists()
    partitions = sorted(p.name for p in (cohort_dir / "dynamic").iterdir())
    assert len(partitions) == 6
    assert all(p.startswith("encounter_id=") for p in partitions)


def test_roundtrip_preserves_everything(cohort: Cohort, cohort_dir: Path) -> None:
    back = load_cohort(cohort_dir)
    assert back.encounter_ids == cohort.encounter_ids
    assert back.static.equals(cohort.static)
    assert back.config == cohort.config
    assert back.dynamic.collect_schema() == cohort.dynamic.collect_schema()
    a, b = cohort.dynamic.collect(), back.dynamic.collect()
    key = [ENCOUNTER_ID, "variable", "time"]
    assert a.sort(key).equals(b.sort(key))


def test_ids_are_strings_even_when_numeric(tmp_path: Path) -> None:
    static = pl.DataFrame({ENCOUNTER_ID: ["10423", "7"], "age": [1, 2]})
    dyn = pl.DataFrame(
        {
            ENCOUNTER_ID: ["10423", "7"],
            "time": [0, 0],
            "variable": ["HR", "HR"],
            "value": [1.0, 2.0],
        }
    ).with_columns(pl.duration(seconds=pl.col("time")).alias("time"))
    save_cohort(Cohort(static, dyn), tmp_path / "num")
    back = load_cohort(tmp_path / "num")
    assert back.dynamic.collect_schema()[ENCOUNTER_ID] == pl.String
    assert back.get("10423").static == {"age": 1}
    assert back.get("7").dynamic["value"].to_list() == [2.0]


def test_save_in_place_is_stable(cohort_dir: Path) -> None:
    before = load_cohort(cohort_dir)
    n_before = before.dynamic.select(pl.len()).collect().item()
    cols_before = before.static.columns
    for _ in range(3):
        c = load_cohort(cohort_dir)
        c.save()
    after = load_cohort(cohort_dir)
    assert after.static.columns == cols_before  # no index columns creeping in
    assert after.dynamic.select(pl.len()).collect().item() == n_before


def test_list_cohorts(cohort_dir: Path, tmp_path: Path) -> None:
    infos = list_cohorts(cohort_dir.parent)
    assert [i.name for i in infos] == ["unit"]
    assert infos[0].n_encounters == 6
    assert list_cohorts(tmp_path / "missing") == []

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from dea.core import ENCOUNTER_ID, EVENT_SCHEMA, Cohort
from dea.io import (
    delete_cohort,
    is_cohort_dir,
    list_cohorts,
    load_cohort,
    save_cohort,
    write_cohort_batches,
)
from dea.io.storage import n_buckets
from dea.synth import generate_cohort, write_synthetic

KEY = [ENCOUNTER_ID, "variable", "time"]


def test_layout(cohort_dir: Path) -> None:
    assert is_cohort_dir(cohort_dir)
    assert (cohort_dir / "cohort.yaml").exists()
    assert (cohort_dir / "static.parquet").exists()
    buckets = sorted(p.name for p in (cohort_dir / "dynamic").iterdir())
    assert buckets == ["bucket=0"]  # six encounters fit one bucket
    assert (cohort_dir / "events" / "bucket=0").exists()


def test_bucket_count() -> None:
    assert n_buckets(1) == 1 and n_buckets(500) == 1 and n_buckets(501) == 2
    assert n_buckets(10**6) == 256


def test_roundtrip_preserves_everything(cohort: Cohort, cohort_dir: Path) -> None:
    back = load_cohort(cohort_dir)
    assert back.encounter_ids == cohort.encounter_ids
    assert back.static.equals(cohort.static)
    assert back.config == cohort.config
    assert back.dynamic.collect_schema() == cohort.dynamic.collect_schema()
    a, b = cohort.dynamic.collect(), back.dynamic.collect()
    assert a.sort(KEY).equals(b.sort(KEY))
    ea, eb = cohort.events.collect(), back.events.collect()
    assert eb.schema == EVENT_SCHEMA
    assert ea.sort(KEY).equals(eb.sort(KEY))
    assert back.get(cohort.encounter_ids[0]).event_variables == ["Note", "Ventilation_Mode"]


def test_files_are_sorted_for_row_group_pruning(cohort_dir: Path) -> None:
    for f in (cohort_dir / "dynamic").rglob("*.parquet"):
        df = pl.read_parquet(f)
        assert df.select(pl.struct(ENCOUNTER_ID, "time").is_sorted()).item()


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
    assert back.get("7").events.height == 0


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


def test_features_cache(tmp_path: Path) -> None:
    path = generate_cohort(4, seed=5, max_hours=30).save(tmp_path / "c")
    c = load_cohort(path)
    cache = path / "derived" / Cohort.FEATURES_CACHE
    assert not cache.exists()
    feats = c.features()
    assert cache.exists()
    assert load_cohort(path).features().equals(feats)
    assert "_features" not in c.list_derived()
    sub = c.select(c.encounter_ids[:2])
    assert sub.features().height == 2
    c.save()  # saving invalidates the cache
    assert not cache.exists()


def test_feature_variable_cap(cohort: Cohort) -> None:
    cohort.config.feature_variables = ["HR"]
    try:
        feats = cohort._compute_features()
        agg_cols = [c for c in feats.columns if "__" in c]
        assert agg_cols == [f"HR__{a}" for a in ("min", "max", "mean", "first", "last", "count")]
    finally:
        cohort.config.feature_variables = None


def test_write_batches_and_streaming_synth(tmp_path: Path) -> None:
    path = write_synthetic(tmp_path / "big", n_encounters=30, seed=1, batch_size=7, max_hours=24)
    c = load_cohort(path)
    assert len(c) == 30 and c.name == "big"
    files = list((path / "dynamic").rglob("*.parquet"))
    assert len(files) == 5  # one file per batch, all in one bucket
    assert c.variables == generate_cohort(2, seed=1, max_hours=24).variables
    eid = c.encounter_ids[17]
    assert c.get(eid).n_observations > 0
    assert c.features().height == 30


def test_write_batches_rejects_nothing_and_handles_no_events(tmp_path: Path) -> None:
    static = pl.DataFrame({ENCOUNTER_ID: ["a"]})
    dyn = pl.DataFrame(
        {ENCOUNTER_ID: ["a"], "time": [0], "variable": ["HR"], "value": [1.0]}
    ).with_columns(pl.duration(seconds=pl.col("time")).alias("time"))
    from dea.core import CohortConfig

    path = write_cohort_batches(tmp_path / "x", CohortConfig(name="x"), [(static, dyn, None)], 1)
    c = load_cohort(path)
    assert c.get("a").n_observations == 1 and c.event_variables == []


def test_export_wide(cohort_dir: Path, tmp_path: Path) -> None:
    c = load_cohort(cohort_dir)
    out = c.export_wide(tmp_path / "wide", every="1h", fill="forward", with_static=True)
    exported = pl.scan_parquet(out / "*.parquet").collect()
    in_memory = c.wide(every="1h", fill="forward", with_static=True)
    key = [ENCOUNTER_ID, "time"]
    assert_frame_equal(exported.sort(key), in_memory.sort(key), check_exact=False, rtol=1e-9)
    sub = c.select(c.encounter_ids[:2])
    out2 = sub.export_wide(tmp_path / "wide2", every="1h")
    assert (
        pl.scan_parquet(out2 / "*.parquet").select(pl.col(ENCOUNTER_ID).n_unique()).collect().item()
        == 2
    )


def test_list_and_delete_cohorts(tmp_path: Path) -> None:
    path = generate_cohort(6, seed=3, name="unit", max_hours=24).save(tmp_path / "unit")
    infos = list_cohorts(tmp_path)
    assert [i.name for i in infos] == ["unit"] and infos[0].n_encounters == 6
    assert list_cohorts(tmp_path / "missing") == []
    (tmp_path / "not_a_cohort").mkdir()
    with pytest.raises(FileNotFoundError):
        delete_cohort(tmp_path / "not_a_cohort")
    delete_cohort(path)
    assert list_cohorts(tmp_path) == []

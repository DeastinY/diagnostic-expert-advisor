from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from dea.core import ENCOUNTER_ID, Cohort
from dea.io import detect, import_csv_tree, import_long, import_path, import_wide, read_table
from dea.synth import write_legacy_csv_tree


def _long_frame(cohort: Cohort) -> pl.DataFrame:
    return cohort.dynamic.collect()


def test_detect_dea_dir(cohort_dir: Path) -> None:
    assert detect(cohort_dir).format == "dea"
    back, report = import_path(cohort_dir)
    assert report.format == "dea" and len(back) == 6


def test_legacy_csv_tree_roundtrip(cohort: Cohort, tmp_path: Path) -> None:
    small = cohort.select(cohort.encounter_ids[:2])
    root = write_legacy_csv_tree(small, tmp_path / "legacy", every="15m")
    assert (root / "static.csv").exists()
    assert detect(root).format == "csv_tree"
    imported, report = import_csv_tree(root, time_unit="m")
    assert report.n_encounters == 2
    assert set(imported.encounter_ids) == set(small.encounter_ids)
    # static joined by id, not by row position
    eid = small.encounter_ids[1]
    assert imported.get(eid).static["age"] == small.get(eid).static["age"]
    assert imported.static.columns == small.static.columns
    # time axis preserved (minutes -> duration)
    w_in = small.get(eid).wide(every="15m", fill="forward")
    w_out = imported.get(eid).wide(every="15m", fill="forward")
    assert w_in.shape == w_out.shape
    assert w_in["HR"].to_list() == pytest.approx(w_out["HR"].to_list(), rel=1e-6)


def test_legacy_tree_with_non_sequential_ids(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    (root / "10423").mkdir(parents=True)
    (root / "7").mkdir()
    pl.DataFrame({"": [10423, 7], "age": [81, 33]}).write_csv(root / "static.csv")
    pl.DataFrame({"": [0, 1, 2], "HR": [60, 61, 62], "note": ["a", "b", "c"]}).write_csv(
        root / "10423" / "dynamic.csv"
    )
    pl.DataFrame({"": [0, 5], "HR": [90, 95]}).write_csv(root / "7" / "dynamic.csv")
    cohort, report = import_csv_tree(root)
    assert cohort.get("10423").static == {"age": 81}
    assert cohort.get("7").static == {"age": 33}
    assert report.event_columns == ["note"] and report.dropped_columns == []
    assert cohort.get("10423").events.get_column("value").to_list() == ["a", "b", "c"]
    assert cohort.get("7").length_of_stay.total_seconds() == 300


def test_long_table_with_datetimes(cohort: Cohort, tmp_path: Path) -> None:
    small = cohort.select(cohort.encounter_ids[:2])
    long = (
        _long_frame(small)
        .with_columns((pl.datetime(2024, 3, 1, 8, 0) + pl.col("time")).alias("charttime"))
        .drop("time")
        .rename({ENCOUNTER_ID: "stay_id", "variable": "itemid", "value": "valuenum"})
    )
    path = tmp_path / "events.csv"
    long.write_csv(path)
    det = detect(path)
    assert det.format == "table" and det.table_kind == "long"
    assert (det.encounter_col, det.time_col, det.variable_col, det.value_col) == (
        "stay_id",
        "charttime",
        "itemid",
        "valuenum",
    )
    assert det.time_unit == "datetime"
    imported, report = import_path(path, name="events")
    assert imported.name == "events"
    assert report.n_observations == long.height
    for eid in small.encounter_ids:
        assert imported.get(eid).length_of_stay == small.get(eid).length_of_stay


def test_wide_table_with_hours_and_static_cols(cohort: Cohort, tmp_path: Path) -> None:
    small = cohort.select(cohort.encounter_ids[:2])
    wide = (
        small.wide(every="1h", variables=["HR", "MAP"])
        .with_columns((pl.col("time").dt.total_seconds() / 3600).alias("hours"))
        .drop("time")
        .join(small.static.select(ENCOUNTER_ID, "age", "sex"), on=ENCOUNTER_ID)
        .rename({ENCOUNTER_ID: "patient"})
    )
    path = tmp_path / "wide.parquet"
    wide.write_parquet(path)
    det = detect(path)
    assert det.table_kind == "wide" and det.encounter_col == "patient" and det.time_col == "hours"
    assert det.time_unit == "h"
    imported, report = import_path(path, static_cols=["age", "sex"])
    assert imported.variables == ["HR", "MAP"]
    assert set(imported.static.columns) == {ENCOUNTER_ID, "age", "sex"}
    eid = small.encounter_ids[0]
    assert imported.get(eid).static["sex"] == small.get(eid).static["sex"]
    assert imported.get(eid).length_of_stay == small.get(eid).wide(every="1h")["time"].max()


def test_import_wide_keeps_text_columns_as_events() -> None:
    df = pl.DataFrame(
        {
            "id": ["x", "x", "y"],
            "minutes": [0, 10, 0],
            "HR": [70, 72, 80],
            "comment": ["fine", "ok", "bad"],
            "SpO2": ["98", "97", "n/a"],
        }
    )
    cohort, report = import_wide(df, "id", "minutes", "m")
    assert report.event_columns == ["comment"] and report.dropped_columns == []
    assert cohort.variables == ["HR", "SpO2"]
    assert cohort.get("y").series("SpO2").height == 0  # 'n/a' became null and was dropped
    assert cohort.get("y").events.get_column("value").to_list() == ["bad"]
    assert report.n_events == 3


def test_import_long_with_separate_static_and_orphans() -> None:
    df = pl.DataFrame(
        {
            "enc": ["a", "a", "b", "b"],
            "t": [0, 1, 0, 1],
            "var": ["HR"] * 4,
            "val": ["1", "2", "x", "3"],
        }
    )
    static = pl.DataFrame({ENCOUNTER_ID: ["a", "ghost"], "age": [50, 60]})
    cohort, report = import_long(df, "enc", "t", "var", "val", time_unit="h", static=static)
    assert set(cohort.encounter_ids) == {"a", "b", "ghost"}
    assert cohort.get("b").static == {"age": None}
    assert cohort.get("b").n_observations == 1
    assert report.n_events == 1  # the non-numeric 'x' became an event
    assert cohort.get("b").events.get_column("value").to_list() == ["x"]
    assert any("no static row" in w for w in report.warnings)
    assert any("no observations" in w for w in report.warnings)


def test_detect_unknown_dir(tmp_path: Path) -> None:
    (tmp_path / "junk.txt").write_text("hello")
    det = detect(tmp_path)
    assert det.format == "unknown"
    with pytest.raises(ValueError):
        import_path(tmp_path)


def test_read_table_formats(tmp_path: Path) -> None:
    df = pl.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    df.write_csv(tmp_path / "t.csv")
    df.write_csv(tmp_path / "t.tsv", separator="\t")
    df.write_parquet(tmp_path / "t.parquet")
    for name in ("t.csv", "t.tsv", "t.parquet"):
        assert read_table(tmp_path / name).equals(df)


def test_detect_finds_layout_below_a_dropped_parent(tmp_path: Path) -> None:
    """A PhysioNet mirror dropped at its top level resolves to the MIMIC folder inside."""
    from dea.io.importers import detect
    from dea.synth import write_mimic_demo

    data = tmp_path / "physionet.org" / "files" / "mimic-iv-demo" / "2.2"
    write_mimic_demo(data, n_stays=4)
    for d in (tmp_path / "physionet.org", data.parent, data, data / "icu"):
        (d / "index.html").write_text("<html></html>")  # wget leaves these everywhere
    (tmp_path / "physionet.org" / "robots.txt").write_text("")
    det = detect(tmp_path / "physionet.org")
    assert det.format == "mimic" and Path(det.source) == data
    # too deep is left alone, and the notes say what was there
    deep = tmp_path / "a" / "b" / "c" / "d" / "e" / "f"
    deep.mkdir(parents=True)
    (deep / "x.csv").write_text("encounter_id,time,v\n1,0,1\n")
    unknown = detect(tmp_path / "a")
    assert unknown.format == "unknown" and any("it contains: b/" in n for n in unknown.notes)

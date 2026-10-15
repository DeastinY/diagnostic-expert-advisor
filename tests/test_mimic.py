from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from dea.io import detect, import_path, is_mimic_dir
from dea.io.mimic import import_mimic
from dea.synth import write_mimic_demo


@pytest.fixture(scope="module")
def mimic_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_mimic_demo(tmp_path_factory.mktemp("mimic"), n_stays=5, seed=3)


def test_layout_matches_mimic(mimic_root: Path) -> None:
    for rel in (
        "icu/icustays",
        "icu/chartevents",
        "icu/d_items",
        "hosp/patients",
        "hosp/admissions",
        "hosp/labevents",
        "hosp/d_labitems",
    ):
        assert (mimic_root / f"{rel}.csv.gz").exists(), rel
    chart = pl.read_csv(mimic_root / "icu/chartevents.csv.gz")
    assert chart.columns[:5] == ["subject_id", "hadm_id", "stay_id", "caregiver_id", "charttime"]
    assert {220045, 223849, 226512} <= set(chart["itemid"].unique())
    assert is_mimic_dir(mimic_root) and detect(mimic_root).format == "mimic"


def test_import_mimic(mimic_root: Path) -> None:
    cohort, report = import_mimic(mimic_root, name="m")
    assert report.format == "mimic-iv" and report.n_encounters == 5
    assert "Heart Rate" in cohort.variables and "Lactate" in cohort.variables
    assert "Creatinine" in cohort.variables  # labs attached via hadm_id + stay window
    assert cohort.event_variables == ["Ventilator Mode"]
    assert {"admission_time", "age", "sex", "outcome", "first_careunit"} <= set(
        cohort.static_columns
    )
    assert cohort.static["admission_time"].dtype == pl.Datetime("us")
    assert cohort.config.variable_units["Heart Rate"] == "bpm"
    enc = cohort.get(cohort.encounter_ids[0])
    assert enc.series("Heart Rate")["time"].min().total_seconds() == 0
    assert set(enc.events["value"].unique()) <= {"CPAP/PSV", "PCV/PSV", "CMV/ASSIST", "Standby"}
    weight = enc.series("Admission Weight (Kg)")
    assert weight.height == 1


def test_import_path_dispatch(mimic_root: Path, tmp_path: Path) -> None:
    cohort, report = import_path(mimic_root, name="demo")
    assert cohort.name == "demo" and report.n_events > 0
    cohort.save(tmp_path / "demo")
    assert (tmp_path / "demo" / "events").exists()

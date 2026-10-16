from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import polars as pl

from dea import charts
from dea.core import Cohort
from dea.core.model import INTERVAL_SCHEMA
from dea.io import import_path, load_cohort
from dea.synth import generate_cohort, write_mimic_demo, write_synthetic


def test_synthetic_intervals_roundtrip(tmp_path: Path) -> None:
    cohort = generate_cohort(12, seed=3, max_hours=96)
    assert set(cohort.interval_variables) <= {"Norepinephrine_infusion", "Ventilation"}
    assert cohort.interval_variables  # at least one sick patient in 12
    feats = cohort.features()
    assert "Ventilation__hours" in feats.columns and feats["Ventilation__hours"].max() > 0
    assert (feats["Ventilation__hours"] >= 0).all()
    assert "Ventilated" in {r.label for r in cohort.config.tags}
    ventilated = cohort.tag_matrix()["Ventilated"].sum()
    assert 0 < ventilated <= 12
    cohort.save(tmp_path / "c")
    assert (tmp_path / "c" / "intervals").exists()
    back = load_cohort(tmp_path / "c")
    assert back.interval_variables == cohort.interval_variables
    eid = back.features().filter(pl.col("Ventilation__hours") > 0)["encounter_id"][0]
    enc = back.get(eid)
    assert enc.intervals.height >= 1 and "Ventilation" in enc.interval_variables
    assert enc.length_of_stay >= enc.intervals["end"].max()
    # sub-cohorts, renames and derive keep the intervals
    sub = back.select([eid])
    assert sub.get(eid).intervals.height == enc.intervals.height
    renamed = back.rename_variables({"Ventilation": "mechanical_ventilation"})
    assert "mechanical_ventilation" in renamed.interval_variables
    derived = back.derive(tmp_path / "sub", '"Ventilation__hours" > 0', name="vent")
    assert derived.interval_variables and len(derived) == ventilated
    # chart draws the interventions rail
    spec = charts.small_multiples(enc, ["HR", "MAP"], "1h").to_dict()
    titles = [p.get("title", {}).get("text") for p in spec["vconcat"]]
    assert "Interventions" in titles


def test_write_synthetic_batches_intervals(tmp_path: Path) -> None:
    write_synthetic(tmp_path / "s", n_encounters=10, batch_size=4, max_hours=48, seed=2)
    cohort = load_cohort(tmp_path / "s")
    assert cohort.intervals.select(pl.len()).collect().item() > 0


def test_mimic_intervals_and_outputs(tmp_path: Path) -> None:
    root = write_mimic_demo(tmp_path / "mimic", n_stays=8, seed=5, max_hours=96)
    for rel in ("icu/inputevents", "icu/procedureevents", "icu/outputevents"):
        assert (root / f"{rel}.csv.gz").exists()
    cohort, report = import_path(root, name="m")
    assert report.n_intervals > 0
    assert set(cohort.interval_variables) <= {"Norepinephrine", "Invasive Ventilation"}
    assert "Foley" in cohort.variables
    assert cohort.config.variable_units["Norepinephrine"] == "mcg/kg/min"
    feats = cohort.features()
    assert "Invasive Ventilation__hours" in feats.columns
    enc = cohort.get(feats.filter(pl.col("Invasive Ventilation__hours") > 0)["encounter_id"][0])
    iv = enc.intervals.filter(pl.col("variable") == "Invasive Ventilation")
    assert iv.height >= 1 and (iv["end"] > iv["start"]).all()
    assert iv["start"].min() >= timedelta(0)


def test_empty_intervals_default() -> None:
    cohort = Cohort(
        pl.DataFrame({"encounter_id": ["a"]}),
        pl.DataFrame(
            {"encounter_id": ["a"], "time": [timedelta(0)], "variable": ["x"], "value": [1.0]}
        ),
    )
    assert cohort.intervals.collect_schema() == pl.Schema(INTERVAL_SCHEMA)
    assert cohort.interval_variables == []
    assert "x__mean" in cohort.features().columns

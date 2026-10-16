"""Unit handling at import and fixes applied before anything is written."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from dea.io import import_path


def _long(rows: list[tuple[str, int, str, float | str, str]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "encounter_id": [r[0] for r in rows],
            "time": [r[1] for r in rows],
            "variable": [r[2] for r in rows],
            "value": [str(r[3]) for r in rows],
            "unit": [r[4] for r in rows],
        }
    )


def test_units_meq_units_split_and_skip(tmp_path: Path) -> None:
    rows: list[tuple] = []
    for i in range(12):
        rows.append(("a", i, "Potassium", 4.1, "mEq/L"))  # monovalent: same as mmol/L
        rows.append(("a", i, "Potassium", 4.0, "mmol/L"))
        rows.append(("a", i, "Heparin Dose (per hour)", 1200.0, "units"))  # not pH
        rows.append(("a", i, "Vancomycin", 15.0, "ug/mL"))  # a level, not the mg dose
        rows.append(("a", i, "WBC", 8.0, "K/uL"))
        rows.append(("a", i, "WBC", 3.0, "#/hpf"))  # urine microscopy under the same name
    _long(rows).write_csv(tmp_path / "obs.csv")
    cohort, report = import_path(tmp_path / "obs.csv", name="u", unit_col="unit")
    reps = {r.variable: r for r in report.unit_reports}
    assert reps["Potassium"].converted == {"meq/L": 12} and not reps["Potassium"].unconvertible
    assert reps["Heparin Dose (per hour)"].converted == {"U": 12}
    assert reps["Vancomycin"].skipped and "Vancomycin" in cohort.variables
    assert reps["WBC"].split and reps["WBC"].unconvertible == {"#/hpf": 12}
    assert "WBC (#/hpf)" in cohort.variables and "WBC" in cohort.variables
    vals = (
        cohort.dynamic.filter(pl.col("variable") == "Vancomycin")
        .select("value")
        .collect()
        .to_series()
    )
    assert vals.min() == 15.0  # untouched
    ids = {c.id: c for c in report.checks}
    assert ids["units-skip:Vancomycin"].severity == "info"
    assert ids["units-split:WBC"].severity == "warning"
    assert ids["units:Potassium"].severity == "info"
    assert not any(c.severity == "error" for c in report.checks)


def test_fixes_at_import(tmp_path: Path) -> None:
    rows: list[tuple] = []
    for i in range(30):
        enc = "abcdef"[i % 6]  # several encounters: top values are counted per encounter
        rows.append((enc, i, "HR", 80.0 + i % 5, ""))
        rows.append((enc, i, "HR", 80.0 + i % 5, ""))  # duplicate
        rows.append((enc, -i, "Lactate", 2.0, ""))  # before admission
        rows.append((enc, i, "Head of Bed", f"{30 + (i // 6) % 3} degrees", ""))
    _long(rows).write_csv(tmp_path / "obs.csv")
    _, before = import_path(tmp_path / "obs.csv", name="f", unit_col="unit")
    ids = {c.id: c for c in before.checks}
    assert ids["duplicates"].recommended and ids["events-numeric:Head of Bed"].recommended
    assert "drop_time" in ids["time:negative"].action and not ids["time:negative"].recommended
    assert before.to_dict()["summary"]["recommended"] == 2

    cohort, after = import_path(
        tmp_path / "obs.csv", name="f", unit_col="unit", fixes="recommended"
    )
    assert set(after.applied) == {"duplicates", "events-numeric:Head of Bed"}
    left = {c.id for c in after.checks}
    assert "duplicates" not in left and "events-numeric:Head of Bed" not in left
    assert "time:negative" in left  # not recommended, so still there
    assert "Head of Bed" in cohort.variables  # parsed into observations
    hob = cohort.dynamic.filter(pl.col("variable") == "Head of Bed").collect()
    assert hob.height == 30 and set(hob["value"].to_list()) == {30.0, 31.0, 32.0}
    assert "Head of Bed" not in cohort.event_variables
    assert [f.description for f in cohort.config.value_fixes] == [
        "duplicate rows removed",
        "numbers parsed from text",
    ]

    cohort, explicit = import_path(
        tmp_path / "obs.csv", name="f", unit_col="unit", fixes=["time:negative"]
    )
    assert explicit.applied == ["time:negative"]
    assert cohort.dynamic.filter(pl.col("time") < pl.duration(seconds=0)).collect().height == 0

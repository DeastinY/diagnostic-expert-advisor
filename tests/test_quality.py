from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import polars as pl

from dea.core import Cohort
from dea.io import import_path, load_cohort
from dea.quality import run_checks
from dea.suggest import apply_suggestions, suggest
from dea.suggest.units import load_units, normalize_units
from dea.synth import generate_cohort


def test_unit_aliases_and_conversions() -> None:
    t = load_units()
    assert t.to_ucum("°F") == "[degF]" and t.to_ucum("mmHg") == "mm[Hg]"
    assert t.to_ucum("K/uL") == "10*9/L" and t.to_ucum("weird") == "weird"
    assert t.conversion("[degF]", "Cel").apply(98.6) == pytest_approx(37.0)
    assert t.conversion("%", "1").apply(40) == pytest_approx(0.4)
    assert t.conversion("mmol/L", "mg/dL", "glucose").apply(5.0) == pytest_approx(90.08, 0.01)
    assert t.conversion("mmol/L", "mg/dL", "sodium") is None  # analyte specific only


def pytest_approx(value: float, tol: float = 1e-6):
    import pytest

    return pytest.approx(value, abs=tol)


def test_normalize_units_lazy() -> None:
    lf = pl.LazyFrame(
        {
            "variable": ["Temp"] * 3 + ["FiO2"] * 2 + ["Other"],
            "value": [98.6, 37.0, 100.4, 50.0, 0.4, 7.0],
            "unit": ["°F", "°C", "F", "%", None, "zorg"],
        }
    )
    out, reports = normalize_units(
        lf, "unit", {"Temp": ("Cel", "temperature"), "FiO2": ("1", "fio2")}
    )
    frame = out.collect()
    assert frame.filter(pl.col("variable") == "Temp")["value"].to_list() == [
        pytest_approx(37.0),
        37.0,
        pytest_approx(38.0),
    ]
    assert frame.filter(pl.col("variable") == "FiO2")["value"].to_list() == [0.5, 0.4]
    assert frame.filter(pl.col("variable") == "Other")["value"].to_list() == [7.0]
    by = {r.variable: r for r in reports}
    assert by["Temp"].converted == {"[degF]": 2} and by["Temp"].mixed
    assert by["FiO2"].converted == {"%": 1}
    assert "Other" not in by


def _mixed_cohort(tmp_path: Path) -> Cohort:
    """Synthetic cohort with 30% of temperatures in °F and a duplicated row."""
    cohort = generate_cohort(8, seed=5, max_hours=48)
    dyn = cohort.dynamic.collect()
    temp = pl.col("variable") == "Temp"
    rows = pl.int_range(pl.len())
    dyn = dyn.with_columns(
        pl.when(temp & (rows % 10 < 3))
        .then(pl.col("value") * 1.8 + 32)
        .otherwise(pl.col("value"))
        .alias("value")
    )
    dyn = pl.concat([dyn, dyn.head(3)])
    out = Cohort(cohort.static, dyn.lazy(), events=cohort.events, config=cohort.config)
    out.save(tmp_path / "mixed")
    return load_cohort(tmp_path / "mixed")


def test_checks_find_units_and_duplicates(tmp_path: Path) -> None:
    cohort = _mixed_cohort(tmp_path)
    checks = {c.id: c for c in run_checks(cohort)}
    temp = checks["range:Temp"]
    assert temp.severity == "warning" and "[degF]" in temp.title
    fix = temp.action["fix"]
    assert fix["variable"] == "Temp" and fix["where"].startswith("value >")
    assert checks["duplicates"].n == 3 and checks["duplicates"].action == {"dedupe": True}
    assert checks["cluster:HR"] if "cluster:HR" in checks else True  # no false two-cluster on HR
    assert not any(c.id.startswith("cluster:") for c in checks.values())


def test_apply_fix_and_dedupe(tmp_path: Path) -> None:
    cohort = _mixed_cohort(tmp_path)
    sugg = {s.id: s for s in suggest(cohort)}
    chosen = [sugg["check:range:Temp"], sugg["check:duplicates"]]
    assert sugg["check:range:Temp"].severity == "warning"
    fixed = apply_suggestions(cohort, chosen)
    fixed.save()
    fixed = load_cohort(tmp_path / "mixed")
    temp = fixed.dynamic.filter(pl.col("variable") == "Temp").collect()["value"]
    assert temp.max() < 45 and temp.min() > 30
    n = fixed.dynamic.select(pl.len()).collect().item()
    assert n == cohort.dynamic.select(pl.len()).collect().item() - 3
    kinds = [(f.variable, f.expr is None) for f in fixed.config.value_fixes]
    assert ("Temp", False) in kinds and ("*", True) in kinds
    assert not any(c.id in ("range:Temp", "duplicates") for c in run_checks(fixed))


def test_import_runs_checks(tmp_path: Path) -> None:
    cohort = generate_cohort(4, seed=1, max_hours=24)
    long = (
        cohort.dynamic.collect()
        .with_columns(
            (pl.lit(0).cast(pl.Int64) + pl.col("time").dt.total_minutes()).alias("minute"),
            pl.when(pl.col("variable") == "Temp")
            .then(pl.lit("°F"))
            .otherwise(pl.lit(""))
            .alias("uom"),
            pl.when(pl.col("variable") == "Temp")
            .then(pl.col("value") * 1.8 + 32)
            .otherwise(pl.col("value"))
            .alias("value"),
        )
        .select("encounter_id", "minute", "variable", "value", "uom")
    )
    long.write_csv(tmp_path / "long.csv")
    from dea.io import detect

    det = detect(tmp_path / "long.csv")
    assert det.table_kind == "long" and det.unit_col == "uom"
    imported, report = import_path(tmp_path / "long.csv", name="u")
    assert report.mapping["unit"] == "uom"
    assert [r.variable for r in report.unit_reports] == ["Temp"]
    temp = imported.dynamic.filter(pl.col("variable") == "Temp").collect()["value"]
    assert 30 < temp.min() and temp.max() < 45
    assert imported.config.variable_units["Temp"] == "°C"
    assert imported.config.variable_codes["Temp"] == "8310-5"
    assert any(c.id == "units:Temp" for c in report.checks)
    assert all(not c.id.startswith("range:Temp") for c in report.checks)


def test_ph_recorded_in_units_is_a_ph() -> None:
    # MIMIC writes valueuom "units" for pH; that is the pH scale, not something to split off
    lf = pl.LazyFrame(
        {
            "variable": ["pH", "pH", "Heparin"],
            "value": [7.4, 7.3, 500.0],
            "unit": ["units", "[pH]", "units"],
        }
    )
    out, reports = normalize_units(lf, "unit", {"pH": ("[pH]", "ph")})
    frame = out.collect()
    assert frame.filter(pl.col("variable") == "pH")["unit"].to_list() == ["[pH]", "[pH]"]
    assert frame["variable"].n_unique() == 2
    (rep,) = reports
    assert rep.converted == {"U": 1} and not rep.unconvertible


def _cohort_with_lead(tmp_path: Path) -> Cohort:
    """Synthetic cohort with a handful of rows shortly before and long before admission."""
    cohort = generate_cohort(6, seed=9, max_hours=48)
    dyn = cohort.dynamic.collect()
    rows = pl.int_range(pl.len())
    dyn = dyn.with_columns(
        pl.when(rows < 8)
        .then(pl.lit(timedelta(hours=-3)))  # emergency department labs
        .when(rows < 13)
        .then(pl.lit(timedelta(hours=-400)))  # a weight from an earlier admission
        .otherwise(pl.col("time"))
        .alias("time")
    )
    out = Cohort(cohort.static, dyn.lazy(), events=cohort.events, config=cohort.config)
    out.save(tmp_path / "lead")
    return load_cohort(tmp_path / "lead")


def test_rows_long_before_admission_are_their_own_check(tmp_path: Path) -> None:
    cohort = _cohort_with_lead(tmp_path)
    checks = {c.id: c for c in run_checks(cohort)}
    early, shortly = checks["time:early"], checks["time:negative"]
    assert early.n == 5 and early.recommended and early.severity == "warning"
    assert early.action == {"drop_time": "early"} and "400 h" in early.detail
    assert shortly.n == 8 and not shortly.recommended and shortly.severity == "info"
    assert shortly.action == {"drop_time": "negative"} and "expected" in shortly.detail

    sugg = {s.id: s for s in suggest(cohort)}
    kept = apply_suggestions(cohort, [sugg["check:time:early"]])
    assert kept.dynamic.select(pl.col("time").min()).collect().item() == timedelta(hours=-3)
    assert [f.where for f in kept.config.value_fixes][-1] == "time < -24h"
    after = {c.id: c for c in run_checks(kept)}
    assert "time:early" not in after and after["time:negative"].n == 8

    none = apply_suggestions(cohort, [sugg["check:time:negative"]])
    assert none.dynamic.select(pl.col("time").min()).collect().item() >= timedelta(0)
    assert not any(c.id.startswith("time:") for c in run_checks(none))

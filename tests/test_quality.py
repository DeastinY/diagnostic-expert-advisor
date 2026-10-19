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
    assert checks["duplicates"].n == 3 and checks["duplicates"].action == {"dedupe": "exact"}
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


def _small_messy_cohort() -> Cohort:
    """Six encounters, three hours: exact repeats, conflicting repeats, text numbers, outliers."""
    from datetime import timedelta

    cohort = generate_cohort(6, seed=11, max_hours=3)
    dyn = cohort.dynamic.collect()
    exact = dyn.head(4)  # the same rows again: exact repeats
    conflict = dyn.slice(10, 3).with_columns((pl.col("value") + 1.0).alias("value"))
    hr = dyn.filter(pl.col("variable") == "HR")
    enc_a, enc_b = cohort.encounter_ids[:2]
    outliers = pl.DataFrame(
        {
            "encounter_id": [enc_a, enc_a, enc_a, enc_b, enc_b, enc_b],
            "time": [timedelta(hours=h, seconds=7) for h in (1, 2, 3, 1, 2, 3)],  # off-grid
            "variable": ["HR"] * 6,
            "value": [900.0, 901.0, 902.0, 903.0, 904.0, 905.0],
        }
    ).cast(dict(hr.schema))
    events = cohort.events.collect()
    encs = cohort.encounter_ids
    hob_rows = [(e, 10 * i, "30 degrees") for i, e in enumerate(encs)]
    hob_rows += [(e, 10 * i + 1, "30 degrees") for i, e in enumerate(encs)]
    hob_rows += [(e, 10 * i + 2, "45 degrees") for i, e in enumerate(encs)]
    hob_rows += [(enc_a, 99, "Flat"), (enc_b, 99, "Flat")]  # too few encounters for the profile
    hob = pl.DataFrame(
        {
            "encounter_id": [r[0] for r in hob_rows],
            "time": [timedelta(minutes=r[1]) for r in hob_rows],
            "variable": ["Head of Bed"] * len(hob_rows),
            "value": [r[2] for r in hob_rows],
        }
    ).cast(dict(events.schema))
    return Cohort(
        cohort.static,
        pl.concat([dyn, exact, conflict, outliers]).lazy(),
        events=pl.concat([events, hob]),
        config=cohort.config,
    )


def test_exact_and_conflicting_duplicates_are_told_apart() -> None:
    from dea.quality.checks import choose

    cohort = _small_messy_cohort()
    checks = {c.id: c for c in run_checks(cohort)}
    exact, conflicting = checks["duplicates"], checks["duplicates:conflicting"]
    assert exact.n == 4 and exact.recommended and exact.action == {"dedupe": "exact"}
    assert "exact" in exact.title and exact.examples["top"][0]["n"] >= 1
    assert conflicting.n == 3 and not conflicting.recommended
    assert [o["id"] for o in conflicting.options] == ["first", "last", "mean", "drop"]
    rows = conflicting.examples["rows"]
    assert len(rows) == 3 and all(len(r["values"]) == 2 for r in rows)
    assert all(abs(r["values"][0] - r["values"][1]) == 1.0 for r in rows)

    n0 = cohort.dynamic.select(pl.len()).collect().item()
    only_exact = apply_suggestions(cohort, choose(list(checks.values()), ["duplicates"]))
    assert only_exact.dynamic.select(pl.len()).collect().item() == n0 - 4
    left = {c.id: c for c in run_checks(only_exact)}
    assert "duplicates" not in left and left["duplicates:conflicting"].n == 3

    key = ["encounter_id", "time", "variable"]
    conflicted = cohort.dynamic.collect().slice(10, 3).select(key)
    for how, expect in (("first", 0.0), ("last", 1.0), ("mean", 0.5)):
        fixed = apply_suggestions(
            cohort, choose(list(checks.values()), [f"duplicates:conflicting#{how}"])
        )
        got = fixed.dynamic.collect().join(conflicted, on=key).sort(key)
        base = cohort.dynamic.collect().slice(10, 3).sort(key)["value"]
        assert got.height == 3, how
        assert (got["value"] - base).to_list() == [expect] * 3, how
    dropped = apply_suggestions(
        cohort, choose(list(checks.values()), ["duplicates:conflicting#drop"])
    )
    assert dropped.dynamic.collect().join(conflicted, on=key).height == 0
    assert "conflicting values at one time: all dropped" in [
        f.description for f in dropped.config.value_fixes
    ]


def test_text_numbers_show_the_real_values() -> None:
    cohort = _small_messy_cohort()
    checks = {c.id: c for c in run_checks(cohort)}
    c = checks["events-numeric:Head of Bed"]
    assert c.n == 18 and c.examples["n_kept"] == 2
    assert "'30 degrees' (12 rows)" in c.detail and "'Flat' (2 rows)" in c.detail
    assert "37.2" not in c.detail
    assert [e["value"] for e in c.examples["parsed"]] == ["30 degrees", "45 degrees"]
    assert [e["value"] for e in c.examples["kept"]] == ["Flat"]


def test_out_of_range_values_offer_drop_clamp_and_a_row_by_row_decision() -> None:
    from dea.quality.checks import choose
    from dea.suggest import load_dictionary

    cohort = _small_messy_cohort()
    checks = {c.id: c for c in run_checks(cohort)}
    c = checks["range:HR:above"]
    hi = float(load_dictionary().by_name["heart_rate"].range[1])
    assert c.n == 6 and not c.recommended
    assert [o["id"] for o in c.options] == ["drop", "clamp"]
    assert c.examples["bounds"][1] == hi and c.examples["before"] == [
        900.0,
        901.0,
        902.0,
        903.0,
        904.0,
    ]
    assert c.examples["unchanged"]["n"] > 100 and c.examples["unchanged"]["median"] < hi
    rows = c.examples["rows"]
    assert len(rows) == 6 and rows[0]["value"] == 905.0 and {r["hours"] for r in rows} == {1, 2, 3}

    hr = lambda co: co.dynamic.filter(pl.col("variable") == "HR").collect()["value"]  # noqa: E731
    clamped = apply_suggestions(cohort, choose(list(checks.values()), ["range:HR:above#clamp"]))
    assert hr(clamped).max() == hi and hr(clamped).len() == hr(cohort).len()
    dropped = apply_suggestions(cohort, choose(list(checks.values()), ["range:HR:above"]))
    assert hr(dropped).max() < hi and hr(dropped).len() == hr(cohort).len() - 6

    enc_a, enc_b = cohort.encounter_ids[:2]
    spec = f"{enc_a}:900.0:drop,{enc_a}:901.0:keep,{enc_b}:905.0:clamp"
    reviewed = apply_suggestions(
        cohort, choose(list(checks.values()), [f"range:HR:above#rows={spec}"])
    )
    values = hr(reviewed)
    assert values.len() == hr(cohort).len() - 1
    assert 900.0 not in values.to_list() and 901.0 in values.to_list()
    assert 905.0 not in values.to_list() and (values == hi).sum() == 1
    assert "duplicates" in {c.id for c in run_checks(reviewed)}  # untouched by the row fixes


def test_choose_keeps_the_choice_in_the_id() -> None:
    from dea.quality.checks import choose

    cohort = _small_messy_cohort()
    checks = run_checks(cohort)
    chosen = choose(
        checks, ["duplicates", "duplicates:conflicting#last", "nonsense", "range:HR:above#bogus"]
    )
    assert [c.id for c in chosen] == [
        "duplicates",
        "duplicates:conflicting#last",
        "range:HR:above",
    ]
    assert chosen[1].action == {"dedupe": "last"}
    assert chosen[2].action["fix"]["expr"] is None  # an unknown option falls back to the default
    assert [c.id for c in choose(checks, "recommended")] == [
        c.id for c in checks if c.action and c.recommended
    ]


def test_run_up_can_be_kept_dropped_or_clamped(tmp_path: Path) -> None:
    from datetime import timedelta

    from dea.quality.checks import choose

    cohort = _cohort_with_lead(tmp_path)
    checks = run_checks(cohort)
    shortly = next(c for c in checks if c.id == "time:negative")
    assert [o["id"] for o in shortly.options] == ["drop", "clamp"]
    assert "run-up" in shortly.detail and "Processing" in shortly.detail
    feats = cohort.features()
    assert {"pre_admission_hours", "n_pre_admission"} <= set(feats.columns)
    assert feats["pre_admission_hours"].max() == 400 and feats["n_pre_admission"].sum() == 13

    clamped = apply_suggestions(cohort, choose(checks, ["time:early", "time:negative#clamp"]))
    times = clamped.dynamic.select(pl.col("time").min()).collect().item()
    assert times == timedelta(0)  # the run-up sits on hour 0, the far-back rows are gone
    n_zero = (
        clamped.dynamic.filter(pl.col("time") == timedelta(0)).select(pl.len()).collect().item()
    )
    assert n_zero >= 8
    assert [f.expr for f in clamped.config.value_fixes][-1] == "time = 0"
    assert clamped.features(refresh=True)["n_pre_admission"].sum() == 0

    # a wider window makes the far-back rows part of the run-up
    wide = Cohort(
        cohort.static,
        cohort.dynamic,
        events=cohort.events,
        config=cohort.config.model_copy(deep=True),
    )
    wide.config.processing.pre_admission_lead_hours = 500
    ids = {c.id for c in run_checks(wide)}
    assert "time:early" not in ids and "time:negative" in ids

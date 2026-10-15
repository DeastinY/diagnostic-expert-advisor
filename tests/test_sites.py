from __future__ import annotations

from pathlib import Path

import polars as pl

from dea import charts
from dea.io import load_cohort
from dea.quality import detect_site_column, run_checks, site_bins, site_checks, site_profile
from dea.suggest import apply_suggestions, suggest
from dea.synth import generate_cohort, write_synthetic


def test_detect_site_column() -> None:
    static = pl.DataFrame(
        {"encounter_id": ["a", "b", "c"], "hospital": ["X", "Y", "X"], "age": [1, 2, 3]}
    )
    assert detect_site_column(static) == "hospital"
    assert detect_site_column(static.drop("hospital")) is None
    assert detect_site_column(static.with_columns(pl.lit("X").alias("hospital"))) is None


def test_multi_site_synthetic_and_checks(tmp_path: Path) -> None:
    cohort = generate_cohort(30, seed=4, max_hours=48, sites=3)
    assert cohort.config.site_column == "site"
    assert set(cohort.static["site"].unique()) == {"Hospital A", "Hospital B", "Hospital C"}
    prof = site_profile(cohort, "site")
    rows = {(r["site"], r["variable"]): r for r in prof.iter_rows(named=True)}
    assert rows[("Hospital B", "Temp")]["median"] > 90  # Fahrenheit
    assert ("Hospital C", "Lactate") not in rows
    assert (
        rows[("Hospital C", "HR")]["interval_s"] == 300
        and rows[("Hospital A", "HR")]["interval_s"] == 60
    )

    found = {c.id: c for c in site_checks(cohort, "site", prof)}
    unit = found["site:unit:Temp:Hospital B"]
    assert unit.severity == "warning" and "[degF]" in unit.title
    assert unit.action["fix"]["encounter_where"] == "site = 'Hospital B'"
    assert found["site:coverage:Lactate"].severity == "warning"
    assert "Hospital C" in found["site:coverage:Lactate"].detail
    assert "site:coverage:CRP" in found
    assert found["site:shift:MAP"].severity in ("info", "warning")
    assert "Hospital B" in found["site:shift:MAP"].detail
    assert "site:sampling:HR" in found and "Hospital C" in found["site:sampling:HR"].title
    assert not any(k.startswith("site:unit:MAP") for k in found)  # offset is not a unit

    bins = site_bins(cohort, "site", ["Temp", "HR"])
    assert set(bins["site"].unique()) == {"Hospital A", "Hospital B", "Hospital C"}
    shares = bins.group_by("variable", "site").agg(pl.col("fraction").sum())
    assert all(abs(f - 1) < 1e-9 for f in shares["fraction"])
    assert charts.site_distributions(bins, dark=True).to_dict()["facet"]
    assert charts.site_matrix(prof, "coverage", "Coverage").to_dict()

    # run_checks picks the configured site column up, and the fix converts only site B
    sugg = {s.id: s for s in suggest(cohort)}
    fix = sugg["check:site:unit:Temp:Hospital B"]
    cohort.save(tmp_path / "ms")
    fixed = apply_suggestions(load_cohort(tmp_path / "ms"), [fix])
    fixed.save()
    fixed = load_cohort(tmp_path / "ms")
    after = site_profile(fixed, "site")
    med = {
        r["site"]: r["median"]
        for r in after.filter(pl.col("variable") == "Temp").iter_rows(named=True)
    }
    assert all(35 < m < 40 for m in med.values())
    assert fixed.config.value_fixes[0].encounter_where == "site = 'Hospital B'"
    ids = {c.id for c in run_checks(fixed)}
    assert "site:unit:Temp:Hospital B" not in ids and "site:coverage:Lactate" in ids


def test_write_synthetic_with_sites(tmp_path: Path) -> None:
    write_synthetic(tmp_path / "s", n_encounters=12, sites=2, batch_size=5, max_hours=24)
    cohort = load_cohort(tmp_path / "s")
    assert cohort.config.site_column == "site" and cohort.static["site"].n_unique() == 2

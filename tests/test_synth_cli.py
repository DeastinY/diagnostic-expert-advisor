from __future__ import annotations

from pathlib import Path

import pytest

from dea.cli import main
from dea.core import Cohort
from dea.io import load_cohort
from dea.synth import generate_cohort


def test_generate_is_deterministic_and_non_sequential() -> None:
    a = generate_cohort(4, seed=3)
    b = generate_cohort(4, seed=3)
    assert a.encounter_ids == b.encounter_ids
    assert a.static.equals(b.static)
    assert all(eid.startswith("ICU-") for eid in a.encounter_ids)
    assert a.dynamic.collect().equals(b.dynamic.collect())


def test_default_config_rules_evaluate(cohort: Cohort) -> None:
    for rule in cohort.config.filters + cohort.config.tags:
        cohort.evaluate(rule.where)
    assert set(cohort.config.plots) <= set(cohort.variables)


def test_cli_synth_info_profile(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "demo"
    assert main(["synth", str(out), "--n", "3", "--seed", "1"]) == 0
    assert load_cohort(out).name == "demo"
    assert main(["info", str(out)]) == 0
    assert "3 encounters" in capsys.readouterr().out
    assert main(["profile", str(out)]) == 0
    assert "suggested grid" in capsys.readouterr().out
    assert main(["profile", str(out), "--json"]) == 0
    assert '"n_encounters": 3' in capsys.readouterr().out


def test_cli_import_and_detect(cohort_dir: Path, tmp_path: Path, capsys) -> None:
    assert main(["detect", str(cohort_dir)]) == 0
    assert '"format": "dea"' in capsys.readouterr().out
    long_csv = tmp_path / "events.csv"
    load_cohort(cohort_dir).dynamic.collect().with_columns(
        __import__("polars").col("time").dt.total_seconds().alias("seconds")
    ).drop("time").write_csv(long_csv)
    out = tmp_path / "imported"
    assert (
        main(
            [
                "import",
                str(long_csv),
                str(out),
                "--time-col",
                "seconds",
                "--time-unit",
                "s",
                "--encounter-col",
                "encounter_id",
                "--variable-col",
                "variable",
                "--value-col",
                "value",
            ]
        )
        == 0
    )
    assert len(load_cohort(out)) == 6


def test_cli_advise_show_payload(cohort_dir: Path, capsys) -> None:
    assert main(["advise", str(cohort_dir), "--show-payload"]) == 0
    assert '"sampling"' in capsys.readouterr().out


def test_cli_rename(tmp_path: Path, capsys) -> None:
    out = tmp_path / "r"
    assert main(["synth", str(out), "--n", "3"]) == 0
    assert main(["rename", str(out), "HR=heart_rate", "MAP=map"]) == 0
    c = load_cohort(out)
    assert "heart_rate" in c.variables and "HR" not in c.variables
    assert c.config.variable_map == {"HR": "heart_rate", "MAP": "map"}
    assert c.config.plots[0] == "heart_rate"


def test_report(cohort_dir: Path, tmp_path: Path) -> None:
    pytest.importorskip("altair")
    out = tmp_path / "report.html"
    assert main(["report", str(cohort_dir), str(out)]) == 0
    text = out.read_text()
    assert "<title>unit · DEA report</title>" in text
    assert "vegaEmbed('#los'" in text and "Heart rate" in text and "Ventilation_Mode" in text
    assert "ICU-" not in text  # no encounter ids in a shareable report

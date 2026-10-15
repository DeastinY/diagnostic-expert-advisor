from __future__ import annotations

from pathlib import Path

from dea.core import Cohort
from dea.io import load_cohort
from dea.io.mimic import import_mimic
from dea.suggest import apply_suggestions, load_dictionary, match_variable, suggest
from dea.synth import generate_cohort, write_mimic_demo


def test_dictionary_matching() -> None:
    d = load_dictionary()
    assert d.match("HR")[0].name == "heart_rate"
    assert d.match("Heart Rate")[0].name == "heart_rate"
    assert d.match("Herzfrequenz")[0].name == "heart_rate"
    assert d.match("220045")[0].name == "heart_rate"
    assert d.match("Horowitz-Quotient_(ohne_Temp-Korrektur)")[0].name == "pao2_fio2"
    assert d.match("Admission Weight (Kg)")[0].name == "weight"
    assert d.match("Arterial Blood Pressure mean")[0].name == "map"
    assert match_variable("completely unknown thing") is None


def test_suggestions_on_synthetic(cohort: Cohort) -> None:
    sugg = {s.id: s for s in suggest(cohort)}
    assert sugg["rename:HR"].action["rename"] == {"HR": "heart_rate"}
    assert sugg["rename:Horowitz"].action["rename"] == {"Horowitz": "pao2_fio2"}
    assert "static:Bed_Number" in sugg
    assert (
        "tag:Hypotension" in sugg
        and sugg["tag:Hypotension"].action["tag"]["where"] == '"MAP__min" < 65'
    )
    assert "tag:Severe ARDS" not in sugg  # already configured on the synthetic cohort
    assert all(
        s.kind in {"harmonize", "static", "drop", "processing", "tag", "filter", "plot", "quality"}
        for s in sugg.values()
    )
    # categorical filters only for categories covering >= 5 encounters (privacy threshold)
    for s in sugg.values():
        if s.kind == "filter":
            assert " = '" in s.action["filter"]["where"]


def test_apply_suggestions_roundtrip(tmp_path: Path) -> None:
    path = generate_cohort(8, seed=11, max_hours=48).save(tmp_path / "c")
    cohort = load_cohort(path)
    chosen = [s for s in suggest(cohort) if s.applicable]
    new = apply_suggestions(cohort, chosen)
    new.save()
    back = load_cohort(path)
    assert "heart_rate" in back.variables and "HR" not in back.variables
    assert "Bed_Number" in back.static_columns and "Bed_Number" not in back.variables
    assert back.config.variable_labels["heart_rate"] == "Heart rate"
    assert back.config.processing.fill_overrides.get("heart_rate") == "interpolate"
    assert back.config.processing.fill_overrides.get("norepinephrine") == "zero"
    labels = {r.label for r in back.config.tags}
    assert {"Hypotension", "Hyperlactatemia"} <= labels
    # renamed rule evaluates on the renamed feature table
    assert back.filter('"map__min" < 65').encounter_ids is not None
    back.tag_matrix()
    # applying again is a no-op for harmonization
    again = [s for s in suggest(back) if s.kind in ("harmonize", "static")]
    assert again == []


def test_suggestions_on_mimic(tmp_path: Path) -> None:
    root = write_mimic_demo(tmp_path / "m", n_stays=4, seed=2)
    cohort, _ = import_mimic(root)
    cohort.save(tmp_path / "mc")
    sugg = {s.id: s for s in suggest(load_cohort(tmp_path / "mc"))}
    assert sugg["rename:Heart Rate"].action["rename"] == {"Heart Rate": "heart_rate"}
    assert "static:Admission Weight (Kg)" in sugg
    assert "plots" in sugg and "Heart Rate" in sugg["plots"].action["plots"]

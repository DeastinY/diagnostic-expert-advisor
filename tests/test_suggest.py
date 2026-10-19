from __future__ import annotations

from pathlib import Path

from dea.core import Cohort
from dea.io import load_cohort
from dea.io.mimic import import_mimic
from dea.suggest import apply_suggestions, load_dictionary, match_variable, suggest
from dea.suggest.engine import unit_fits
from dea.suggest.units import load_units
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
    root = write_mimic_demo(tmp_path / "m", n_stays=4, seed=2, max_hours=72)
    cohort, _ = import_mimic(root)
    cohort.save(tmp_path / "mc")
    sugg = {s.id: s for s in suggest(load_cohort(tmp_path / "mc"))}
    assert sugg["rename:Heart Rate"].action["rename"] == {"Heart Rate": "heart_rate"}
    assert "static:Admission Weight (Kg)" in sugg
    assert "plots" in sugg and "Heart Rate" in sugg["plots"].action["plots"]


def test_matching_keeps_systolic_diastolic_and_mean_apart() -> None:
    # MIMIC's non-invasive items: the fuzzy pass used to send "systolic" to the
    # diastolic concept because the dbp synonym differed by one word
    d = load_dictionary()
    assert d.match("Non Invasive Blood Pressure systolic")[0].name == "sbp"
    assert d.match("Non Invasive Blood Pressure diastolic")[0].name == "dbp"
    assert d.match("Non Invasive Blood Pressure mean")[0].name == "map"
    manual = d.match("Manual Blood Pressure Systolic Left")
    assert manual is None or manual[0].name != "dbp"


def test_drug_level_is_not_renamed_to_the_dose(tmp_path: Path) -> None:
    # MIMIC's lab "Vancomycin" (ug/mL) is the serum level; the dictionary's vancomycin is a
    # dose in mg. The name matches, the unit cannot, so no rename is proposed.
    cohort = generate_cohort(4, seed=3, max_hours=24).rename_variables({"Lactate": "Vancomycin"})
    cohort.config.variable_units["Vancomycin"] = "ug/mL"
    sugg = {s.id: s for s in suggest(cohort)}
    assert "rename:Vancomycin" not in sugg
    assert "Vancomycin" not in (sugg["plots"].action["plots"] if "plots" in sugg else [])
    cohort.config.variable_units["Vancomycin"] = "mg"
    sugg = {s.id: s for s in suggest(cohort)}
    assert sugg["rename:Vancomycin"].action["rename"] == {"Vancomycin": "vancomycin"}


def test_unit_fits_screens_only_concentrations_under_drug_names() -> None:
    d, units = load_dictionary(), load_units()
    vanco, propofol, wbc = d.by_name["vancomycin"], d.by_name["propofol"], d.by_name["wbc"]
    assert not unit_fits(units, "ug/mL", vanco)
    assert not unit_fits(units, "mg/L", vanco) and not unit_fits(units, "umol/L", vanco)
    assert unit_fits(units, "mg", vanco) and unit_fits(units, "", vanco)
    assert unit_fits(units, "mcg/kg/min", propofol)  # a rate in another unit: the normalizer's job
    assert unit_fits(units, "G/L", wbc)  # not a drug: a count per volume is what it should be


def test_drug_in_an_unconvertible_unit_is_not_renamed(tmp_path: Path) -> None:
    # Subcutaneous prophylaxis charted as "dose" under a heparin name is not the infusion in
    # U/h; a unit that cannot be converted into the concept's unit at all blocks the rename.
    # A rate in another unit still converts, so it is renamed.
    cohort = generate_cohort(4, seed=3, max_hours=24)
    cohort = cohort.rename_variables({"Lactate": "Heparin Sodium"})
    cohort.config.variable_units["Heparin Sodium"] = "dose"
    sugg = {s.id: s for s in suggest(cohort)}
    assert "rename:Heparin Sodium" not in sugg
    cohort.config.variable_units["Heparin Sodium"] = "units/hour"
    sugg = {s.id: s for s in suggest(cohort)}
    assert sugg["rename:Heparin Sodium"].action["rename"] == {"Heparin Sodium": "heparin"}

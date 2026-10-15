from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from dea.io import detect, import_path, load_cohort
from dea.standards import diagnosis_table, groups_for, parse_icd
from dea.standards.fhir import export_fhir, import_fhir, is_fhir_source
from dea.suggest import load_dictionary
from dea.synth import generate_cohort, write_mimic_demo


def test_icd_parsing_and_groups() -> None:
    cases = {
        ("A41.9", 10): ("A419", "Infectious diseases", ["sepsis"]),
        ("038.9", 9): ("0389", "Infectious diseases", ["sepsis"]),
        ("J80", None): ("J80", "Respiratory system", ["ards"]),
        ("518.82", None): ("51882", "Respiratory system", ["ards"]),
        ("E11.9", None): ("E119", "Endocrine and metabolic", ["diabetes"]),
        ("E880.0", None): ("E8800", "External causes", []),
        ("V45.81", None): ("V4581", "Factors influencing health status", []),
        ("428.0", 9): ("4280", "Circulatory system", ["heart_failure"]),
        ("I50.9", 10): ("I509", "Circulatory system", ["heart_failure"]),
        ("U07.1", 10): ("U071", "Special purposes", ["covid19"]),
    }
    for (code, version), (norm, chapter, groups) in cases.items():
        parsed = parse_icd(code, version)
        assert parsed.code == norm and parsed.chapter == chapter, code
        assert groups_for(parsed) == groups, code
    assert parse_icd("A41.9").dotted == "A41.9" and parse_icd("0389", 9).dotted == "038.9"
    assert parse_icd("", 10) is None


def test_diagnosis_table_merges_versions() -> None:
    long = pl.DataFrame(
        {
            "encounter_id": ["a", "a", "b", "c"],
            "icd_code": ["A41.9", "J80", "0389", "I10"],
            "icd_version": [10, 10, 9, 10],
            "seq_num": [2, 1, 1, 1],
        }
    )
    table = diagnosis_table(long)
    assert table["icd_codes"].to_list() == ["J80|A41.9", "038.9", "I10"]
    assert table["dx_sepsis"].to_list() == [True, True, False]
    assert table["primary_diagnosis"].to_list() == ["J80", "038.9", "I10"]
    assert table["primary_chapter"][2] == "Circulatory system"


def test_dictionary_has_codes() -> None:
    d = load_dictionary()
    assert d.by_loinc["8867-4"].name == "heart_rate"
    assert d.match("2524-7")[0].name == "lactate"  # LOINC code as variable name
    assert all(c.ucum for c in d.concepts)


def test_mimic_import_harmonizes(tmp_path: Path) -> None:
    root = write_mimic_demo(tmp_path / "mimic", n_stays=20, seed=2)
    cohort, report = import_path(root, name="m")
    units = {r.variable: r for r in report.unit_reports}
    assert (
        "Temperature Fahrenheit" in units and "[degF]" in units["Temperature Fahrenheit"].converted
    )
    assert "Inspired O2 Fraction" in units and "%" in units["Inspired O2 Fraction"].converted
    temps = cohort.dynamic.filter(pl.col("variable") == "Temperature Fahrenheit").collect()
    assert 30 < temps["value"].min() and temps["value"].max() < 45
    fio2 = cohort.dynamic.filter(pl.col("variable") == "Inspired O2 Fraction").collect()
    assert fio2["value"].max() <= 1.0
    assert cohort.config.variable_units["Temperature Fahrenheit"] == "°C"
    assert cohort.config.variable_codes["Heart Rate"] == "8867-4"
    assert {"icd_codes", "primary_diagnosis", "dx_sepsis", "dx_heart_failure"} <= set(
        cohort.static_columns
    )
    assert cohort.static["n_diagnoses"].min() >= 1
    ids = {c.id for c in report.checks}
    assert "units:Temperature Fahrenheit" in ids and "units:Inspired O2 Fraction" in ids
    assert not any(i.startswith("range:") for i in ids)
    # both temperature items harmonize onto one concept
    from dea.suggest import suggest

    renames = {s.id: s for s in suggest(cohort) if s.kind == "harmonize"}
    assert renames["rename:Temperature Fahrenheit"].action["rename"] == {
        "Temperature Fahrenheit": "temperature"
    }
    assert renames["rename:Temperature Celsius"].action["rename"] == {
        "Temperature Celsius": "temperature"
    }
    # diagnosis groups show up as filter suggestions (for groups with >= 5 encounters)
    ids = {s.id for s in suggest(cohort)}
    flags = [
        c for c in cohort.static_columns if c.startswith("dx_") and cohort.static[c].sum() >= 5
    ]
    assert flags and all(f"filter:{c} = true" in ids for c in flags)


def test_fhir_roundtrip(tmp_path: Path) -> None:
    cohort = generate_cohort(5, seed=9, max_hours=36)
    static = cohort.static.with_columns(
        pl.Series("icd_codes", ["A41.9|J80", "428.0", None, "E11.9", "I10|N17.9"]),
        pl.Series("age", [71, 45, 60, 33, 80]),
    )
    cohort = type(cohort)(static, cohort.dynamic, events=cohort.events, config=cohort.config)
    cohort.save(tmp_path / "src")
    cohort = load_cohort(tmp_path / "src")
    out = tmp_path / "fhir"
    written = export_fhir(cohort, out)
    assert written["Encounter"] == 5 and written["Condition"] == 6
    assert written["Observation"] == cohort.dynamic.select(pl.len()).collect().item()
    assert is_fhir_source(out) and detect(out).format == "fhir"
    back, report = import_fhir(out, name="back")
    assert report.format == "fhir" and len(back) == 5
    assert "heart_rate" in back.variables and back.config.variable_codes["heart_rate"] == "8867-4"
    assert back.static["age"].to_list() == [71, 45, 60, 33, 80]
    assert back.static["dx_sepsis"].to_list() == [True, False, None, False, False]
    assert (
        back.static.filter(pl.col("encounter_id") == cohort.encounter_ids[1])["icd_codes"][0]
        == "428.0"
    )
    hr_src = cohort.dynamic.filter(pl.col("variable") == "HR").collect().sort("time")
    hr_back = back.dynamic.filter(pl.col("variable") == "heart_rate").collect().sort("time")
    assert hr_back.height == hr_src.height
    assert hr_back["value"].to_list() == pytest.approx(hr_src["value"].to_list())
    assert hr_back["time"].to_list() == hr_src["time"].to_list()


def test_fhir_bundle_with_components_and_loose_observations(tmp_path: Path) -> None:
    import json

    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {
                "resource": {
                    "resourceType": "Patient",
                    "id": "p1",
                    "gender": "female",
                    "birthDate": "1960",
                }
            },
            {
                "resource": {
                    "resourceType": "Encounter",
                    "id": "e1",
                    "subject": {"reference": "Patient/p1"},
                    "period": {"start": "2024-01-01T10:00:00Z", "end": "2024-01-03T10:00:00Z"},
                }
            },
            {
                "resource": {
                    "resourceType": "Observation",
                    "id": "bp",
                    "subject": {"reference": "Patient/p1"},
                    "effectiveDateTime": "2024-01-01T11:00:00Z",
                    "code": {"coding": [{"system": "http://loinc.org", "code": "85354-9"}]},
                    "component": [
                        {
                            "code": {"coding": [{"system": "http://loinc.org", "code": "8480-6"}]},
                            "valueQuantity": {"value": 120, "unit": "mmHg"},
                        },
                        {
                            "code": {"coding": [{"system": "http://loinc.org", "code": "8462-4"}]},
                            "valueQuantity": {"value": 80, "unit": "mmHg"},
                        },
                    ],
                }
            },
            {
                "resource": {
                    "resourceType": "Observation",
                    "id": "t",
                    "encounter": {"reference": "Encounter/e1"},
                    "effectiveDateTime": "2024-01-01T12:00:00Z",
                    "code": {"coding": [{"system": "http://loinc.org", "code": "8310-5"}]},
                    "valueQuantity": {"value": 100.4, "unit": "degF", "code": "[degF]"},
                }
            },
            {
                "resource": {
                    "resourceType": "Observation",
                    "id": "s",
                    "encounter": {"reference": "Encounter/e1"},
                    "effectiveDateTime": "2024-01-01T12:00:00Z",
                    "code": {"text": "Ventilation mode"},
                    "valueString": "PSV",
                }
            },
            {
                "resource": {
                    "resourceType": "Condition",
                    "id": "c1",
                    "subject": {"reference": "Patient/p1"},
                    "encounter": {"reference": "Encounter/e1"},
                    "code": {
                        "coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "J80"}]
                    },
                }
            },
            {"resource": {"resourceType": "MedicationRequest", "id": "m1"}},
        ],
    }
    file = tmp_path / "bundle.json"
    file.write_text(json.dumps(bundle))
    assert detect(file).format == "fhir"
    cohort, report = import_path(file, name="b")
    assert len(cohort) == 1 and set(cohort.variables) == {"sbp", "8462-4", "temperature"}
    assert cohort.event_variables == ["Ventilation mode"]
    temp = cohort.dynamic.filter(pl.col("variable") == "temperature").collect()["value"][0]
    assert temp == pytest.approx(38.0, abs=0.01)
    assert cohort.static["age"][0] == 63 and cohort.static["sex"][0] == "female"
    assert cohort.static["dx_ards"][0] is True
    assert "1 MedicationRequest resource(s) ignored" in report.warnings

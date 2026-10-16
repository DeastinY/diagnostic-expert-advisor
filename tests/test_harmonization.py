from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from dea.advise import Advisor, MappingProposals
from dea.io import import_path, load_cohort
from dea.quality import site_casemix
from dea.standards.fhir import export_fhir, import_fhir
from dea.standards.mapping import apply_mapping, load_mapping, load_units, mapping_report
from dea.suggest import load_dictionary, suggest
from dea.synth import generate_cohort


def test_mapping_file_roundtrip(tmp_path: Path) -> None:
    (tmp_path / "site.yaml").write_text(
        'heart_rate: ["HR", "Herzfrequenz"]\n'
        "map: ABP mean\n"
        '"Ventilation": ventilation_episode\n'
        "units:\n  ABP mean: mmHg\n"
    )
    mapping = load_mapping(tmp_path / "site.yaml")
    assert mapping == {
        "HR": "heart_rate",
        "Herzfrequenz": "heart_rate",
        "map": "ABP mean",
        "Ventilation": "ventilation_episode",
    }
    assert load_units(tmp_path / "site.yaml") == {"ABP mean": "mmHg"}
    cohort = generate_cohort(6, seed=1, max_hours=24)
    info = mapping_report(cohort, {"HR": "heart_rate", "nope": "x", "Ventilation": "vent"})
    assert info["applied"] == ["HR", "Ventilation"] and info["unused"] == ["nope"]
    out = apply_mapping(cohort, {"HR": "heart_rate", "Ventilation": "vent"})
    assert "heart_rate" in out.variables and "vent" in out.interval_variables
    assert out.config.variable_codes["heart_rate"] == "8867-4"
    assert out.config.variable_labels["heart_rate"] == "Heart rate"
    assert out.config.variable_map["HR"] == "heart_rate"


class _FakeCompletions:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def parse(self, **kwargs):  # noqa: ANN003
        model = kwargs["response_format"]
        parsed = model.model_validate(self.payload)
        msg = type("Msg", (), {"parsed": parsed, "refusal": None})()
        return type("C", (), {"choices": [type("Ch", (), {"message": msg})()]})()


def test_advisor_bulk_mapping_with_fake_client() -> None:
    payload = {
        "proposals": [
            {
                "variable": "Bed_Number",
                "concept": "",
                "confidence": 0.9,
                "rationale": "not a measurement",
            },
            {
                "variable": "PCT",
                "concept": "procalcitonin",
                "confidence": 0.95,
                "rationale": "abbreviation",
            },
            {"variable": "X", "concept": "made_up", "confidence": 0.7, "rationale": "?"},
        ]
    }
    client = type(
        "Client", (), {"chat": type("Chat", (), {"completions": _FakeCompletions(payload)})()}
    )()
    adv = Advisor(client=client, model="fake", base_url="http://x", api_key="k")
    props = adv.map_variables([{"name": "PCT"}], [{"name": "procalcitonin"}])
    assert isinstance(props, MappingProposals)
    assert props.as_mapping() == {
        "PCT": "procalcitonin",
        "X": "made_up",
    }  # endpoint filters unknowns


def test_medication_concepts_and_interval_tags() -> None:
    d = load_dictionary()
    assert d.match("Norepinephrine_infusion")[0].name == "norepinephrine"
    assert d.match("Noradrenalin")[0].name == "norepinephrine"
    assert d.match("221906")[0].name == "norepinephrine" and d.by_name["norepinephrine"].atc
    assert d.match("Propofol")[0].role == "medication"
    cohort = generate_cohort(10, seed=4, max_hours=72)
    sugg = {s.id: s for s in suggest(cohort)}
    assert sugg["rename:Norepinephrine_infusion"].action["rename"] == {
        "Norepinephrine_infusion": "norepinephrine"
    }
    tag = sugg["tag:Received norepinephrine"]
    assert tag.action["tag"]["where"] == '"Norepinephrine_infusion__hours" > 0'


def test_site_casemix() -> None:
    cohort = generate_cohort(24, seed=2, max_hours=48, sites=3)
    cm = site_casemix(cohort, "site")
    assert set(cm["site"]) == {"Hospital A", "Hospital B", "Hospital C"}
    assert {"n_encounters", "los_median_h", "age_median", "female_share", "mortality"} <= set(
        cm.columns
    )
    assert cm["n_encounters"].sum() == 24 and (cm["female_share"] <= 1).all()


def test_fhir_medication_administration_roundtrip(tmp_path: Path) -> None:
    cohort = generate_cohort(6, seed=8, max_hours=48)
    assert cohort.interval_variables
    cohort.save(tmp_path / "c")
    cohort = load_cohort(tmp_path / "c")
    written = export_fhir(cohort, tmp_path / "fhir")
    n = cohort.intervals.select(pl.len()).collect().item()
    assert written["MedicationAdministration"] == n
    first = json.loads(
        (tmp_path / "fhir" / "MedicationAdministration.ndjson").read_text().splitlines()[0]
    )
    assert first["effectivePeriod"]["start"] and "rateQuantity" in first["dosage"]
    back, report = import_fhir(tmp_path / "fhir", name="back")
    assert report.n_intervals == n
    assert "norepinephrine" in back.interval_variables or "Ventilation" in back.interval_variables
    hours = back.features()
    cols = [c for c in hours.columns if c.endswith("__hours")]
    assert cols and hours.select(cols).sum().row(0)[0] > 0


def test_cli_import_with_mapping(tmp_path: Path) -> None:
    from dea.cli import main
    from dea.synth import write_mimic_demo

    root = write_mimic_demo(tmp_path / "mimic", n_stays=4, seed=1)
    (tmp_path / "site.yaml").write_text('heart_rate: ["Heart Rate"]\n')
    assert (
        main(["import", str(root), str(tmp_path / "out"), "--mapping", str(tmp_path / "site.yaml")])
        == 0
    )
    cohort = load_cohort(tmp_path / "out")
    assert "heart_rate" in cohort.variables and "Heart Rate" not in cohort.variables
    assert (
        main(["rename", str(tmp_path / "out"), "--mapping-file", str(tmp_path / "site.yaml")]) == 0
    )
    _ = import_path  # keep the import used

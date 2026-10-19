from __future__ import annotations

import json

import polars as pl

from dea.core import ENCOUNTER_ID, Cohort
from dea.profile import profile_cohort


def test_profile_basics(cohort: Cohort) -> None:
    prof = profile_cohort(cohort)
    assert prof.n_encounters == 6
    assert prof.n_variables == len(cohort.variables)
    names = {v.name for v in prof.variables}
    assert "HR" in names and "Horowitz" in names
    hr = next(v for v in prof.variables if v.name == "HR")
    assert hr.coverage == 1.0
    assert hr.median_interval_s == 60
    assert "integer" in hr.flags
    lab = next(v for v in prof.variables if v.name == "Leukocytes")
    assert lab.median_interval_s == 43200
    bed = next(v for v in prof.variables if v.name == "Bed_Number")
    assert "constant_per_encounter" in bed.flags
    assert any("Bed_Number" in w for w in prof.warnings)
    assert prof.sampling.suggested_resample == "1m"
    assert prof.sampling.regular_fraction is not None and prof.sampling.regular_fraction > 0.5


def test_static_profiles(cohort: Cohort) -> None:
    prof = profile_cohort(cohort)
    by_name = {s.name: s for s in prof.static_columns}
    assert by_name["age"].kind == "numeric" and by_name["age"].min is not None
    assert by_name["sex"].kind == "categorical"
    # k-anonymity style threshold: 6 encounters, categories need >= 5 to be listed
    assert all(count >= 5 for _, count in by_name["sex"].top_values)
    assert ENCOUNTER_ID not in by_name


def test_prompt_payload_contains_no_patient_level_data(cohort: Cohort) -> None:
    prof = profile_cohort(cohort)
    text = json.dumps(prof.to_prompt_dict())
    for eid in cohort.encounter_ids:
        assert eid not in text
    payload = prof.to_prompt_dict()
    assert set(payload) == {
        "cohort",
        "sampling",
        "variables",
        "events",
        "intervals",
        "static_columns",
        "warnings",
    }
    assert {e["name"] for e in payload["events"]} == {"Note", "Ventilation_Mode"}
    assert all({"name", "n_episodes", "coverage"} <= set(i) for i in payload["intervals"])
    vent = next(e for e in payload["events"] if e["name"] == "Ventilation_Mode")
    assert set(vent["categories"] or []) <= {"PSV", "PCV", "VCV", "none"}
    note = next(e for e in payload["events"] if e["name"] == "Note")
    assert note["categories"] is None  # free text never reaches the advisor
    assert all("values" not in v for v in payload["variables"])
    sex = next(s for s in payload["static_columns"] if s["name"] == "sex")
    assert sex["categories"] is None or all(c in ("M", "F") for c in sex["categories"])


def test_profile_frames_and_json(cohort: Cohort) -> None:
    prof = profile_cohort(cohort)
    vf = prof.variables_frame()
    assert {"name", "coverage", "interval", "flags"} <= set(vf.columns)
    assert prof.static_frame().height == len(cohort.static_columns)
    assert json.loads(prof.to_json())["n_encounters"] == 6


def test_flags_on_handcrafted_data() -> None:
    dyn = pl.DataFrame(
        {
            ENCOUNTER_ID: ["a"] * 12 + ["b"] * 12,
            "time": list(range(12)) * 2,
            "variable": ["ID"] * 12 + ["Flag"] * 12,
            "value": [float(i) for i in range(12)] + [0.0, 1.0] * 6,
        }
    ).with_columns(pl.duration(hours=pl.col("time")).alias("time"))
    static = pl.DataFrame({ENCOUNTER_ID: ["a", "b", "c"], "note": ["free text"] * 3})
    prof = profile_cohort(Cohort(static, dyn))
    by = {v.name: v for v in prof.variables}
    assert "suspected_identifier" in by["ID"].flags
    assert "binary" in by["Flag"].flags
    assert any("no observations" in w for w in prof.warnings)
    assert any("identifier" in w for w in prof.warnings)
    assert next(s for s in prof.static_columns if s.name == "note").kind == "categorical"


def test_profile_groups_constant_warnings() -> None:
    # a real ICU export has hundreds of constant items; one warning per kind, not per item
    from dea.profile.profiler import _grouped

    assert _grouped(["a", "b"], "is constant", "are constant") == [
        "'a' is constant",
        "'b' is constant",
    ]
    many = [f"v{i}" for i in range(9)]
    (line,) = _grouped(many, "is constant", "are constant")
    assert line.startswith("9 variables are constant: 'v0', 'v1', 'v2', 'v3', 'v4' and 4 more")

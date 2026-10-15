from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from dea.advise import Advisor, AdvisorError, AdvisorSettings, Recommendation
from dea.core import Cohort
from dea.profile import profile_cohort

REC = {
    "summary": "Minute-level vitals with sparse labs.",
    "resample_every": "15m",
    "aggregate": "mean",
    "default_fill": "forward",
    "variables": [
        {
            "variable": "HR",
            "label": "Heart rate",
            "role": "vital_sign",
            "unit_guess": "1/min",
            "imputation": "interpolate",
            "rationale": "continuous signal",
        },
        {
            "variable": "Bed_Number",
            "label": "Bed",
            "role": "identifier",
            "unit_guess": "",
            "imputation": "drop",
            "rationale": "not a measurement",
        },
        {
            "variable": "Ghost",
            "label": "Ghost",
            "role": "other",
            "unit_guess": "",
            "imputation": "zero",
            "rationale": "does not exist",
        },
    ],
    "suggested_filters": [
        {"label": "Severe ARDS", "where": '"Horowitz__min" < 100', "description": "dup"},
        {"label": "Elderly", "where": "age >= 80", "description": "80+"},
    ],
    "plot_variables": ["HR", "MAP", "Ghost"],
    "storage_notes": ["Parquet is fine."],
    "warnings": ["Bed_Number looks like a device id."],
}


class _FakeCompletions:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls: list[str] = []

    def parse(self, **kwargs):
        self.calls.append("parse")
        if self.mode == "structured":
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            parsed=Recommendation.model_validate(REC), refusal=None
                        )
                    )
                ]
            )
        if self.mode == "refusal":
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(parsed=None, refusal="no"))]
            )
        raise RuntimeError("response_format not supported")

    def create(self, **kwargs):
        self.calls.append("create")
        assert kwargs["response_format"] == {"type": "json_object"}
        content = json.dumps(REC) if self.mode == "json" else '{"summary": 1}'
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _client(mode: str):
    completions = _FakeCompletions(mode)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


@pytest.fixture
def profile(cohort: Cohort):
    return profile_cohort(cohort)


def test_structured_path(profile) -> None:
    client, fake = _client("structured")
    rec = Advisor(client=client, model="m").recommend(profile)
    assert rec.resample_every == "15m"
    assert fake.calls == ["parse"]


def test_json_fallback(profile) -> None:
    client, fake = _client("json")
    rec = Advisor(client=client, model="m").recommend(profile)
    assert rec.summary.startswith("Minute")
    assert fake.calls == ["parse", "create"]


def test_errors(profile) -> None:
    client, _ = _client("broken")
    with pytest.raises(AdvisorError, match="did not match"):
        Advisor(client=client, model="m").recommend(profile)
    client, _ = _client("refusal")
    with pytest.raises(AdvisorError, match="no structured answer"):
        Advisor(client=client, model="m").recommend(profile)


def test_payload_guard(cohort: Cohort) -> None:
    with pytest.raises(TypeError):
        Advisor.payload(cohort)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Advisor.payload(cohort.dynamic.collect())  # type: ignore[arg-type]


def test_apply_to_config(cohort: Cohort) -> None:
    rec = Recommendation.model_validate(REC)
    cfg = rec.apply_to(cohort.config, cohort.variables)
    assert cfg.processing.resample_every == "15m" and cfg.processing.fill == "forward"
    assert cfg.processing.fill_overrides == {"HR": "interpolate"}
    assert cfg.processing.drop_variables == ["Bed_Number"]
    assert "Ghost" not in cfg.variable_labels and cfg.variable_labels["HR"] == "Heart rate"
    labels = [r.label for r in cfg.filters]
    assert labels.count("Severe ARDS") == 1 and "Elderly" in labels
    assert cfg.plots == ["HR", "MAP"]
    assert cohort.config.plots != cfg.plots  # original untouched


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "DEA_AI_MODEL",
        "DEA_AI_BASE_URL",
        "DEA_AI_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    assert not AdvisorSettings.from_env().configured
    monkeypatch.setenv("DEA_AI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("DEA_AI_MODEL", "llama3")
    s = AdvisorSettings.from_env()
    assert s.configured and s.model == "llama3" and "localhost" in s.describe()
    assert AdvisorSettings.from_env(model="other").model == "other"

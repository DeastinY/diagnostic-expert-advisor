"""Ask an OpenAI-compatible model for processing recommendations.

Works with OpenAI, Azure OpenAI, and any server speaking the same API (vLLM, Ollama,
LiteLLM, llama.cpp, ...). Configure through the constructor or environment variables:

``DEA_AI_MODEL``, ``DEA_AI_BASE_URL``, ``DEA_AI_API_KEY`` (falling back to the SDK's own
``OPENAI_API_KEY`` / ``OPENAI_BASE_URL``).

The advisor accepts a :class:`~dea.profile.CohortProfile` only. It never receives
encounter identifiers, timestamps or individual values; :meth:`Advisor.payload` shows the
exact JSON that leaves the machine.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from dea.advise.schema import Recommendation
from dea.profile.profiler import CohortProfile

SYSTEM_PROMPT = """You are a clinical data engineering assistant inside the Diagnostic Expert
Advisor (DEA), a tool that turns heterogeneous ICU / hospital time-series cohorts into
analysis-ready data.

You receive a *profile* of a cohort: aggregate statistics only (no patient-level data).
Variables are measurements over time since admission; static columns describe each encounter.

Recommend how to process and store the cohort for exploration and machine learning:
- a resampling grid that respects the dominant sampling interval (do not oversample sparse labs;
  do not throw away minute-level vitals unless the cohort is huge),
- an aggregate for cells with several observations,
- a default gap-filling strategy and per-variable deviations (labs usually forward-fill,
  continuous vitals may interpolate, medications and binary flags should be zero- or forward-filled,
  identifiers/constants should be dropped),
- a short English label, role and likely unit for each variable (names may be German or
  abbreviated),
- a handful of clinically meaningful encounter filters expressed over the feature table
  (columns are "<variable>__min|max|mean|first|last|count", los_hours, n_observations and
  the static columns; quote names containing special characters with double quotes),
- up to eight variables worth plotting by default,
- storage notes and warnings about data quality or privacy (e.g. free-text or identifier columns).

Only reference variables and static columns that exist in the profile. Be concrete and brief."""


class AdvisorError(RuntimeError):
    """The advisor could not produce a recommendation."""


@dataclass(frozen=True)
class AdvisorSettings:
    model: str
    base_url: str | None
    api_key: str | None
    timeout: float = 120.0

    @classmethod
    def from_env(cls, **overrides: Any) -> AdvisorSettings:
        values = {
            "model": os.environ.get("DEA_AI_MODEL", "gpt-4o-mini"),
            "base_url": os.environ.get("DEA_AI_BASE_URL") or os.environ.get("OPENAI_BASE_URL"),
            "api_key": os.environ.get("DEA_AI_API_KEY") or os.environ.get("OPENAI_API_KEY"),
        }
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)

    @property
    def configured(self) -> bool:
        return bool(self.api_key or self.base_url)

    def describe(self) -> str:
        where = self.base_url or "api.openai.com"
        key = "key set" if self.api_key else "no key"
        return f"model={self.model} endpoint={where} ({key})"


def _sdk_available() -> bool:
    try:
        import openai  # noqa: F401
    except ImportError:
        return False
    return True


class Advisor:
    """Thin client around ``chat.completions`` with structured output and a JSON fallback."""

    def __init__(
        self, settings: AdvisorSettings | None = None, client: Any = None, **overrides: Any
    ) -> None:
        self.settings = settings or AdvisorSettings.from_env(**overrides)
        self._client = client

    @staticmethod
    def available() -> bool:
        return _sdk_available() and AdvisorSettings.from_env().configured

    @property
    def client(self) -> Any:
        if self._client is None:
            if not _sdk_available():
                raise AdvisorError("the 'openai' package is not installed: pip install dea[ai]")
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self.settings.api_key or "not-needed",
                base_url=self.settings.base_url,
                timeout=self.settings.timeout,
            )
        return self._client

    @staticmethod
    def payload(profile: CohortProfile) -> dict[str, Any]:
        """Exactly what is sent to the model."""
        if not isinstance(profile, CohortProfile):
            raise TypeError("Advisor only accepts a CohortProfile; never pass raw data")
        return profile.to_prompt_dict()

    def recommend(self, profile: CohortProfile) -> Recommendation:
        payload = self.payload(profile)
        user = "Cohort profile (JSON):\n" + json.dumps(payload, indent=1, default=str)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
        try:
            return self._structured(messages)
        except AdvisorError:
            raise
        except Exception as first:
            try:
                return self._json_fallback(messages)
            except Exception as second:
                raise AdvisorError(
                    f"structured output failed ({first}); JSON fallback failed ({second})"
                ) from second

    def _structured(self, messages: list[dict[str, str]]) -> Recommendation:
        completion = self.client.chat.completions.parse(
            model=self.settings.model,
            messages=messages,
            response_format=Recommendation,
        )
        choice = completion.choices[0]
        parsed = getattr(choice.message, "parsed", None)
        if parsed is None:
            refusal = getattr(choice.message, "refusal", None)
            raise AdvisorError(
                f"model returned no structured answer{': ' + refusal if refusal else ''}"
            )
        return parsed

    def _json_fallback(self, messages: list[dict[str, str]]) -> Recommendation:
        schema = json.dumps(Recommendation.model_json_schema())
        messages = [
            messages[0],
            {
                "role": "system",
                "content": "Answer with a single JSON object matching this JSON schema, "
                "nothing else:\n" + schema,
            },
            messages[1],
        ]
        completion = self.client.chat.completions.create(
            model=self.settings.model,
            messages=messages,
            response_format={"type": "json_object"},
        )
        text = completion.choices[0].message.content or ""
        try:
            return Recommendation.model_validate_json(text)
        except ValidationError as exc:
            raise AdvisorError(f"model answer did not match the schema: {exc}") from exc

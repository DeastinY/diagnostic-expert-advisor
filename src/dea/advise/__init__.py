"""Optional AI advisor (OpenAI-compatible API). Sees profiles, never patient data."""

from dea.advise.advisor import Advisor, AdvisorError, AdvisorSettings
from dea.advise.schema import Recommendation, SuggestedRule, VariableAdvice

__all__ = [
    "Advisor",
    "AdvisorError",
    "AdvisorSettings",
    "Recommendation",
    "SuggestedRule",
    "VariableAdvice",
]

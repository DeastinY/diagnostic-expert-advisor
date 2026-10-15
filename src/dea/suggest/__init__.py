"""Rule-based suggestions after import: harmonization, processing, tags, quality.

Works fully offline; the AI advisor enriches these, it is not required for them.
"""

from dea.suggest.engine import Suggestion, apply_suggestions, suggest
from dea.suggest.vocab import Concept, Dictionary, load_dictionary, match_variable

__all__ = [
    "Concept",
    "Dictionary",
    "Suggestion",
    "apply_suggestions",
    "load_dictionary",
    "match_variable",
    "suggest",
]

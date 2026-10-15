"""Connectors to local NLP models for free text: NER, de-identification, LLM extraction.

Every connector implements :class:`Annotator`; :func:`annotate_cohort` runs one over the
text in a cohort (string events and text static columns) and stores the entities under
``derived/entities.parquet``. Models run locally; nothing leaves the machine.
"""

from dea.nlp.base import Annotator, Entity, FunctionAnnotator
from dea.nlp.connectors import (
    CONNECTORS,
    GlinerAnnotator,
    LLMAnnotator,
    PresidioAnnotator,
    SpacyAnnotator,
    TransformersAnnotator,
    available_connectors,
    make_annotator,
)
from dea.nlp.pipeline import annotate_cohort, entities_to_rules, entity_summary, redact_cohort

__all__ = [
    "CONNECTORS",
    "Annotator",
    "Entity",
    "FunctionAnnotator",
    "GlinerAnnotator",
    "LLMAnnotator",
    "PresidioAnnotator",
    "SpacyAnnotator",
    "TransformersAnnotator",
    "annotate_cohort",
    "available_connectors",
    "entities_to_rules",
    "entity_summary",
    "make_annotator",
    "redact_cohort",
]

from __future__ import annotations

import re
from pathlib import Path

import polars as pl
import pytest

from dea.core import Cohort
from dea.io import load_cohort
from dea.nlp import (
    Entity,
    FunctionAnnotator,
    annotate_cohort,
    available_connectors,
    entities_to_rules,
    entity_summary,
    make_annotator,
)
from dea.nlp.pipeline import attach_entity_flags

DRUGS = re.compile(
    r"norepinephrine|meropenem|propofol|heparin|furosemide|hydrocortisone|piperacillin-tazobactam",
    re.I,
)
DX = re.compile(
    r"septic shock|pneumonia|ARDS|acute kidney injury|cardiogenic shock|COPD exacerbation"
    r"|pulmonary embolism",
    re.I,
)


def regex_annotator() -> FunctionAnnotator:
    def find(text: str) -> list[Entity]:
        ents = [
            Entity(m.group(), "medication", m.start(), m.end(), 1.0, m.group().lower())
            for m in DRUGS.finditer(text)
        ]
        ents += [
            Entity(m.group(), "disease", m.start(), m.end(), 1.0, m.group().lower())
            for m in DX.finditer(text)
        ]
        return ents

    return FunctionAnnotator(find, name="regex")


def test_pipeline_with_function_annotator(cohort: Cohort, tmp_path: Path) -> None:
    path = cohort.save(tmp_path / "c")
    c = load_cohort(path)
    assert "Note" in c.event_variables
    entities = annotate_cohort(c, regex_annotator(), event_variables=["Note"])
    assert entities.height > 0
    assert set(entities["label"].unique()) <= {"medication", "disease"}
    assert (path / "derived" / "entities.parquet").exists()
    assert entities["source"].unique().to_list() == ["event"]
    summary = entity_summary(entities, len(c), min_encounters=1)
    assert {"label", "entity", "encounters", "mentions", "coverage"} <= set(summary.columns)
    rules = entities_to_rules(c, entities, min_encounters=1, max_rules=3)
    assert len(rules) == 3 and rules[0].label.startswith("mentions: ")
    flagged = attach_entity_flags(c, entities, min_encounters=1, max_columns=5)
    cols = [col for col in flagged.static.columns if col.startswith("mentions__")]
    assert 1 <= len(cols) <= 5
    assert flagged.static[cols[0]].dtype == pl.Boolean
    assert flagged.filter(f'"{cols[0]}" = true').encounter_ids  # usable as a filter right away


def test_static_text_columns_and_empty(cohort: Cohort) -> None:
    c = Cohort(
        cohort.static.with_columns(pl.lit("history of pneumonia").alias("history")),
        cohort.dynamic,
        events=cohort.events,
        config=cohort.config,
    )
    ents = annotate_cohort(
        c, regex_annotator(), event_variables=["nothing"], static_columns=["history"]
    )
    assert ents["source"].unique().to_list() == ["static"] and ents.height == len(c)
    none = annotate_cohort(c, regex_annotator(), event_variables=["nothing"])
    assert none.is_empty()


def test_available_and_unknown() -> None:
    avail = available_connectors()
    assert set(avail) == {"spacy", "gliner", "transformers", "presidio", "llm"}
    with pytest.raises(ValueError):
        make_annotator("nope")


def test_spacy_connector(cohort: Cohort) -> None:
    spacy = pytest.importorskip("spacy")
    try:
        spacy.load("en_core_web_sm")
    except OSError:
        pytest.skip("en_core_web_sm not installed")
    ann = make_annotator("spacy", model="en_core_web_sm")
    out = ann.annotate(["Patient transferred from Boston General on March 3 for septic shock."])
    labels = {e.label for e in out[0]}
    assert labels & {"GPE", "ORG", "DATE", "FAC"}
    ents = annotate_cohort(cohort, ann, event_variables=["Note"])
    assert ents.height > 0 and ents.schema["start"] == pl.Int64


def test_presidio_connector_redacts() -> None:
    pytest.importorskip("presidio_analyzer")
    spacy = pytest.importorskip("spacy")
    try:
        spacy.load("en_core_web_sm")
    except OSError:
        pytest.skip("en_core_web_sm not installed")
    ann = make_annotator("presidio")
    text = "John Smith, phone 617-555-0199, admitted 2024-03-03 with pneumonia."
    ents = ann.annotate([text])[0]
    assert {e.label for e in ents} & {"PERSON", "PHONE_NUMBER", "DATE_TIME"}
    redacted = ann.redact([text])[0]
    assert (
        "John Smith" not in redacted and "617-555-0199" not in redacted and "pneumonia" in redacted
    )

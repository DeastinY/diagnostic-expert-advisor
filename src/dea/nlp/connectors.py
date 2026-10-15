"""Concrete connectors. Each imports its library lazily and explains how to install it.

| connector      | extra               | what it is good for                                   |
|----------------|---------------------|-------------------------------------------------------|
| spacy          | dea[nlp-spacy]      | any spaCy pipeline: en_core_web_*, scispaCy, medspaCy  |
| gliner         | dea[nlp-gliner]     | zero-shot NER with your own label list, CPU friendly  |
| transformers   | dea[nlp-transformers] | Hugging Face token-classification models (clinical NER) |
| presidio       | dea[deid]           | PII detection and redaction before anything else      |
| llm            | dea[ai]             | any OpenAI-compatible local endpoint (Ollama, vLLM)   |

Model weights are downloaded once into ``DEA_MODELS_DIR`` (default ``~/.dea/models``) by
``dea nlp install <connector>``; afterwards everything runs offline.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dea.nlp.base import Annotator, Entity

DEFAULT_LABELS = [
    "disease",
    "symptom",
    "medication",
    "procedure",
    "lab test",
    "anatomy",
    "device",
    "dosage",
]


def models_dir() -> Path:
    path = Path(os.environ.get("DEA_MODELS_DIR", Path.home() / ".dea" / "models"))
    path.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(path / "huggingface"))
    return path


def _missing(package: str, extra: str) -> RuntimeError:
    return RuntimeError(f"{package} is not installed: pip install 'dea[{extra}]'")


class SpacyAnnotator:
    """spaCy pipeline NER. Works with scispaCy (``en_core_sci_sm``, ``en_ner_bc5cdr_md``) and
    medspaCy pipelines, which add clinical context such as negation; when a ``context``
    component is present, negated entities get the label suffix ``:negated``."""

    name = "spacy"

    def __init__(self, model: str = "en_core_web_sm", **kwargs: Any) -> None:
        try:
            import spacy
        except ImportError as exc:
            raise _missing("spacy", "nlp-spacy") from exc
        self.model = model
        self.nlp = spacy.load(model, **kwargs)

    def annotate(self, texts: Sequence[str]) -> list[list[Entity]]:
        out = []
        for doc in self.nlp.pipe(texts, batch_size=64):
            ents = []
            for e in doc.ents:
                label = e.label_
                if getattr(e._, "is_negated", False):
                    label += ":negated"
                ents.append(Entity(e.text, label, e.start_char, e.end_char, 1.0, e.kb_id_ or None))
            out.append(ents)
        return out

    @staticmethod
    def install(model: str = "en_core_web_sm") -> None:
        from spacy.cli import download

        download(model)


class GlinerAnnotator:
    """Zero-shot NER: pass the entity types you care about, no training needed."""

    name = "gliner"

    def __init__(
        self,
        model: str = "urchade/gliner_medium-v2.1",
        labels: Sequence[str] | None = None,
        threshold: float = 0.5,
    ) -> None:
        try:
            from gliner import GLiNER
        except ImportError as exc:
            raise _missing("gliner", "nlp-gliner") from exc
        models_dir()
        self.model = model
        self.labels = list(labels or DEFAULT_LABELS)
        self.threshold = threshold
        self._model = GLiNER.from_pretrained(model)

    def annotate(self, texts: Sequence[str]) -> list[list[Entity]]:
        out = []
        for batch in self._model.batch_predict_entities(
            list(texts), self.labels, threshold=self.threshold
        ):
            out.append(
                [
                    Entity(e["text"], e["label"], e["start"], e["end"], float(e["score"]))
                    for e in batch
                ]
            )
        return out

    @staticmethod
    def install(model: str = "urchade/gliner_medium-v2.1") -> None:
        from gliner import GLiNER

        models_dir()
        GLiNER.from_pretrained(model)


class TransformersAnnotator:
    """Hugging Face token-classification pipeline (e.g. ``d4data/biomedical-ner-all``)."""

    name = "transformers"

    def __init__(self, model: str = "d4data/biomedical-ner-all", device: int | str = -1) -> None:
        try:
            from transformers import pipeline
        except ImportError as exc:
            raise _missing("transformers", "nlp-transformers") from exc
        models_dir()
        self.model = model
        self._pipe = pipeline(
            "token-classification", model=model, aggregation_strategy="simple", device=device
        )

    def annotate(self, texts: Sequence[str]) -> list[list[Entity]]:
        out = []
        for res in self._pipe(list(texts), batch_size=16):
            out.append(
                [
                    Entity(
                        r["word"],
                        r["entity_group"],
                        int(r["start"]),
                        int(r["end"]),
                        float(r["score"]),
                    )
                    for r in res
                ]
            )
        return out

    @staticmethod
    def install(model: str = "d4data/biomedical-ner-all") -> None:
        from transformers import pipeline

        models_dir()
        pipeline("token-classification", model=model)


class PresidioAnnotator:
    """PII detection (names, dates, phone numbers, ids, locations) for de-identification."""

    name = "presidio"

    def __init__(
        self,
        language: str = "en",
        spacy_model: str = "en_core_web_sm",
        entities: Sequence[str] | None = None,
    ) -> None:
        try:
            from presidio_analyzer import AnalyzerEngine
            from presidio_analyzer.nlp_engine import NlpEngineProvider
            from presidio_anonymizer import AnonymizerEngine
        except ImportError as exc:
            raise _missing("presidio-analyzer", "deid") from exc
        provider = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": language, "model_name": spacy_model}],
            }
        )
        self.language = language
        self.entities = list(entities) if entities else None
        self._analyzer = AnalyzerEngine(
            nlp_engine=provider.create_engine(), supported_languages=[language]
        )
        self._anonymizer = AnonymizerEngine()

    def annotate(self, texts: Sequence[str]) -> list[list[Entity]]:
        out = []
        for text in texts:
            results = self._analyzer.analyze(
                text=text, language=self.language, entities=self.entities
            )
            out.append(
                [
                    Entity(text[r.start : r.end], r.entity_type, r.start, r.end, float(r.score))
                    for r in results
                ]
            )
        return out

    def redact(self, texts: Sequence[str]) -> list[str]:
        """Replace detected PII with ``<ENTITY_TYPE>`` placeholders."""
        out = []
        for text in texts:
            results = self._analyzer.analyze(
                text=text, language=self.language, entities=self.entities
            )
            out.append(self._anonymizer.anonymize(text=text, analyzer_results=results).text)
        return out


class LLMAnnotator:
    """Entity extraction through an OpenAI-compatible endpoint, e.g. a local Ollama model.

    Uses the same settings as the advisor (``DEA_AI_BASE_URL``, ``DEA_AI_MODEL``). Slower than
    the dedicated NER models but handles any label list and language without training.
    """

    name = "llm"

    def __init__(self, labels: Sequence[str] | None = None, **settings: Any) -> None:
        from dea.advise.advisor import Advisor

        self.labels = list(labels or DEFAULT_LABELS)
        self._advisor = Advisor(**settings)

    def annotate(self, texts: Sequence[str]) -> list[list[Entity]]:
        client = self._advisor.client
        out = []
        for text in texts:
            prompt = (
                "Extract entities from the clinical text below. Entity types: "
                + ", ".join(self.labels)
                + '. Answer with JSON {"entities": [{"text": ..., "label": ...}]} only.\n\n'
                + text
            )
            res = client.chat.completions.create(
                model=self._advisor.settings.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            try:
                items = json.loads(res.choices[0].message.content or "{}").get("entities", [])
            except json.JSONDecodeError:
                items = []
            ents = []
            for it in items:
                span = str(it.get("text", ""))
                start = text.find(span)
                if span and start >= 0:
                    ents.append(
                        Entity(span, str(it.get("label", "entity")), start, start + len(span), 0.9)
                    )
            out.append(ents)
        return out


CONNECTORS: dict[str, type] = {
    "spacy": SpacyAnnotator,
    "gliner": GlinerAnnotator,
    "transformers": TransformersAnnotator,
    "presidio": PresidioAnnotator,
    "llm": LLMAnnotator,
}
_PACKAGES = {
    "spacy": "spacy",
    "gliner": "gliner",
    "transformers": "transformers",
    "presidio": "presidio_analyzer",
    "llm": "openai",
}


def available_connectors() -> dict[str, bool]:
    """Which connectors have their library installed."""
    import importlib.util

    return {name: importlib.util.find_spec(pkg) is not None for name, pkg in _PACKAGES.items()}


def make_annotator(name: str, **kwargs: Any) -> Annotator:
    try:
        cls = CONNECTORS[name]
    except KeyError:
        raise ValueError(f"unknown connector {name!r}; choose from {sorted(CONNECTORS)}") from None
    return cls(**kwargs)

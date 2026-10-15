# Local NLP connectors

Free text in a cohort (string events such as notes, text static columns) can be annotated
with models that run on your machine. Nothing leaves it.

| Connector | Install | Models | Good for |
|---|---|---|---|
| `spacy` | `dea[nlp-spacy]`, then `dea nlp install spacy --model en_core_web_sm` | any spaCy pipeline; scispaCy (`en_core_sci_sm`, `en_ner_bc5cdr_md`) and medspaCy for clinical NER with negation | fast, CPU, well understood |
| `gliner` | `dea[nlp-gliner]`, then `dea nlp install gliner` | `urchade/gliner_medium-v2.1` and biomedical GLiNER variants | zero-shot: pass your own entity types, no training |
| `transformers` | `dea[nlp-transformers]` | any Hugging Face token-classification model, e.g. `d4data/biomedical-ner-all` | strongest supervised clinical NER |
| `presidio` | `dea[deid]` | spaCy-based recognizers | PII detection and redaction before sharing |
| `llm` | `dea[ai]` | any OpenAI-compatible endpoint, e.g. Ollama | any labels and language, slower |

Model weights are stored under `DEA_MODELS_DIR` (default `~/.dea/models`). The default
`torch` wheel is the CUDA build (~550 MB); use the CPU wheel from the PyTorch index on
machines without a GPU.

## Running

```bash
dea nlp run data/demo --connector spacy --events Note
dea nlp run data/demo --connector gliner --labels disease medication procedure "lab test"
```

Results go to `derived/entities.parquet` with one row per entity (encounter, time, source,
variable, text, label, offsets, score, normalized form). `dea.nlp.entity_summary` aggregates
them with a minimum of five encounters per entity so summaries stay non-identifying;
`attach_entity_flags` adds boolean `mentions__<entity>` columns to the static table so filters
such as `"mentions__sepsis" = true` work immediately; `entities_to_rules` proposes tags.

## Programmatic use

```python
import dea
from dea.nlp import annotate_cohort, make_annotator, redact_cohort

cohort = dea.load_cohort("data/demo")
ann = make_annotator("spacy", model="en_core_sci_sm")
entities = annotate_cohort(cohort, ann, event_variables=["Note"])

pii = make_annotator("presidio")
dea.save_cohort(redact_cohort(cohort, pii, event_variables=["Note"]), "data/demo_deid")
```

Any object with an `annotate(texts) -> list[list[Entity]]` method is a connector;
`FunctionAnnotator` wraps a plain function for rules or tests.

# AI advisor

The advisor turns a cohort profile into concrete processing recommendations: a resampling
grid, an aggregate, gap-filling per variable, labels and units, suggested encounter
filters, variables worth plotting, and warnings about identifiers or free text.

It talks to any **OpenAI-compatible** chat-completions endpoint and is entirely optional
(`pip install dea[ai]`).

## Configuration

| Variable | Meaning |
|---|---|
| `DEA_AI_MODEL` | model name (default `gpt-4o-mini`; for local servers any name the server accepts) |
| `DEA_AI_BASE_URL` | endpoint, e.g. `http://localhost:11434/v1` for Ollama, `http://host:8000/v1` for vLLM |
| `DEA_AI_API_KEY` | API key; falls back to `OPENAI_API_KEY` |

The UI lets you override all three per session.

## What is sent

Only `CohortProfile.to_prompt_dict()`: cohort size, length-of-stay statistics, sampling
statistics, and per-variable / per-static-column aggregates (range, percentiles, mean,
coverage, typical interval, flags). No encounter ids, no timestamps, no individual values.
Categories of static columns appear only when the column has few distinct values and each
listed category covers at least five encounters.

```bash
dea advise data/my_cohort --show-payload   # print exactly what would be sent
dea advise data/my_cohort                  # get a recommendation (JSON)
dea advise data/my_cohort --apply          # write it into cohort.yaml
```

## Canonical names

Each variable also gets a `canonical_name` (for example `heart_rate` for a local `HF` code).
`dea advise COHORT --apply --rename`, or the checkbox in the UI, renames variables in the data
and in every config reference (filters, tags, plots, labels); the mapping is recorded under
`variable_map` in `cohort.yaml`. Cohorts from different hospitals become comparable once they
share canonical names. `dea rename COHORT OLD=NEW` does the same by hand.

## How answers are obtained

1. `chat.completions.parse` with the `Recommendation` schema (structured outputs).
2. If the server rejects structured outputs, a JSON-mode fallback with the schema in the
   prompt, validated with pydantic.

Recommendations are data, not commands: nothing changes until you press **Apply** (or pass
`--apply`), and the result is a plain `cohort.yaml` you can read and edit.

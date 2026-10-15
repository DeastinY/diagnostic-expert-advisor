"""Run an annotator over a cohort's text and turn entities into data."""

from __future__ import annotations

from collections.abc import Sequence

import polars as pl

from dea.core.config import Rule
from dea.core.model import ENCOUNTER_ID, TIME, VALUE, VARIABLE, Cohort, collect
from dea.nlp.base import Annotator

ENTITY_SCHEMA = {
    ENCOUNTER_ID: pl.String(),
    TIME: pl.Duration("us"),
    "source": pl.String(),
    VARIABLE: pl.String(),
    "text": pl.String(),
    "label": pl.String(),
    "start": pl.Int64(),
    "end": pl.Int64(),
    "score": pl.Float64(),
    "normalized": pl.String(),
}
MIN_TEXT_LEN = 3


def _texts(
    cohort: Cohort, event_variables: Sequence[str] | None, static_columns: Sequence[str] | None
) -> pl.DataFrame:
    """All text to annotate: (encounter_id, time, source, variable, text)."""
    ev = cohort.events
    if event_variables:
        ev = ev.filter(pl.col(VARIABLE).is_in(list(event_variables)))
    events = collect(ev).select(
        ENCOUNTER_ID, TIME, pl.lit("event").alias("source"), VARIABLE, pl.col(VALUE).alias("text")
    )
    frames = [events]
    cols = [c for c in (static_columns or []) if c in cohort.static.columns]
    for c in cols:
        frames.append(
            cohort.static.select(
                ENCOUNTER_ID,
                pl.lit(None, dtype=pl.Duration("us")).alias(TIME),
                pl.lit("static").alias("source"),
                pl.lit(c).alias(VARIABLE),
                pl.col(c).cast(pl.String()).alias("text"),
            )
        )
    texts = pl.concat(frames) if frames else pl.DataFrame(schema={ENCOUNTER_ID: pl.String()})
    return texts.filter(
        pl.col("text").is_not_null() & (pl.col("text").str.len_chars() >= MIN_TEXT_LEN)
    )


def annotate_cohort(
    cohort: Cohort,
    annotator: Annotator,
    event_variables: Sequence[str] | None = None,
    static_columns: Sequence[str] | None = None,
    name: str = "entities",
    batch_size: int = 256,
) -> pl.DataFrame:
    """Find entities in events (and optional static text columns); store under ``derived/<name>``.

    Identical texts are annotated once. Returns one row per entity.
    """
    texts = _texts(cohort, event_variables, static_columns)
    if texts.is_empty():
        frame = pl.DataFrame(schema=ENTITY_SCHEMA)
    else:
        unique = texts.get_column("text").unique().to_list()
        found: dict[str, list] = {}
        for i in range(0, len(unique), batch_size):
            batch = unique[i : i + batch_size]
            for text, ents in zip(batch, annotator.annotate(batch), strict=True):
                found[text] = ents
        rows = []
        for row in texts.iter_rows(named=True):
            for e in found.get(row["text"], []):
                rows.append(
                    {
                        ENCOUNTER_ID: row[ENCOUNTER_ID],
                        TIME: row[TIME],
                        "source": row["source"],
                        VARIABLE: row[VARIABLE],
                        "text": e.text,
                        "label": e.label,
                        "start": e.start,
                        "end": e.end,
                        "score": e.score,
                        "normalized": e.normalized,
                    }
                )
        frame = (
            pl.DataFrame(rows, schema=ENTITY_SCHEMA) if rows else pl.DataFrame(schema=ENTITY_SCHEMA)
        )
    if cohort.root is not None:
        out = cohort.root / "derived"
        out.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(out / f"{name}.parquet")
    return frame


def entity_summary(
    entities: pl.DataFrame, n_encounters: int, min_encounters: int = 5
) -> pl.DataFrame:
    """Entities by label and normalized text with encounter counts (k-anonymity threshold)."""
    if entities.is_empty():
        return pl.DataFrame(
            schema={
                "label": pl.String(),
                "entity": pl.String(),
                "encounters": pl.Int64(),
                "mentions": pl.Int64(),
                "coverage": pl.Float64(),
            }
        )
    key = pl.coalesce(pl.col("normalized"), pl.col("text").str.to_lowercase()).alias("entity")
    return (
        entities.with_columns(key)
        .group_by("label", "entity")
        .agg(pl.col(ENCOUNTER_ID).n_unique().alias("encounters"), pl.len().alias("mentions"))
        .filter(pl.col("encounters") >= min_encounters)
        .with_columns((pl.col("encounters") / n_encounters).round(3).alias("coverage"))
        .sort(["encounters", "label", "entity"], descending=[True, False, False])
    )


def entities_to_rules(
    cohort: Cohort,
    entities: pl.DataFrame,
    labels: Sequence[str] | None = None,
    min_encounters: int = 5,
    max_rules: int = 20,
) -> list[Rule]:
    """Suggest tags such as ``mentions: sepsis`` from frequent entities.

    Rules reference a derived boolean table written to ``derived/entity_flags.parquet`` and
    joined into the feature table through ``static``-like columns named ``mentions__<entity>``.
    """
    summary = entity_summary(entities, len(cohort), min_encounters)
    if labels:
        summary = summary.filter(pl.col("label").is_in(list(labels)))
    rules = []
    for label, entity in summary.head(max_rules).select("label", "entity").iter_rows():
        col = f"mentions__{entity}"
        rules.append(
            Rule(
                label=f"mentions: {entity}",
                where=f'"{col}" = true',
                description=f"{label} '{entity}' mentioned in free text",
            )
        )
    return rules


def attach_entity_flags(
    cohort: Cohort, entities: pl.DataFrame, min_encounters: int = 5, max_columns: int = 50
) -> Cohort:
    """Add boolean ``mentions__<entity>`` columns to the static table for frequent entities."""
    summary = entity_summary(entities, len(cohort), min_encounters).head(max_columns)
    if summary.is_empty():
        return cohort
    key = pl.coalesce(pl.col("normalized"), pl.col("text").str.to_lowercase()).alias("entity")
    flags = (
        entities.with_columns(key)
        .filter(pl.col("entity").is_in(summary["entity"].to_list()))
        .select(ENCOUNTER_ID, "entity")
        .unique()
        .with_columns(pl.lit(True).alias("v"))
        .pivot(on="entity", index=ENCOUNTER_ID, values="v")
    )
    flags = flags.rename({c: f"mentions__{c}" for c in flags.columns if c != ENCOUNTER_ID})
    static = cohort.static.drop([c for c in cohort.static.columns if c.startswith("mentions__")])
    static = static.join(flags, on=ENCOUNTER_ID, how="left").with_columns(
        [pl.col(c).fill_null(False) for c in flags.columns if c != ENCOUNTER_ID]
    )
    return Cohort(
        static,
        cohort.dynamic,
        events=cohort.events,
        config=cohort.config,
        root=cohort.root,
        _full=cohort._full,
    )


def redact_cohort(
    cohort: Cohort,
    annotator,
    event_variables: Sequence[str] | None = None,
    static_columns: Sequence[str] | None = None,
) -> Cohort:
    """Replace PII in text events and static text columns using an annotator with ``redact``."""
    if not hasattr(annotator, "redact"):
        raise TypeError("annotator has no redact(); use PresidioAnnotator")
    ev = collect(cohort.events)
    mask = pl.col(VARIABLE).is_in(list(event_variables)) if event_variables else pl.lit(True)
    target = ev.filter(mask)
    if target.height:
        unique = target.get_column(VALUE).unique().to_list()
        mapping = dict(zip(unique, annotator.redact(unique), strict=True))
        ev = ev.with_columns(
            pl.when(mask).then(pl.col(VALUE).replace(mapping)).otherwise(pl.col(VALUE)).alias(VALUE)
        )
    static = cohort.static
    for c in static_columns or []:
        if c in static.columns:
            vals = static.get_column(c).drop_nulls().cast(pl.String()).unique().to_list()
            mapping = dict(zip(vals, annotator.redact(vals), strict=True))
            static = static.with_columns(pl.col(c).cast(pl.String()).replace(mapping).alias(c))
    return Cohort(
        static,
        cohort.dynamic,
        events=ev,
        config=cohort.config,
        root=cohort.root,
        _full=cohort._full,
    )

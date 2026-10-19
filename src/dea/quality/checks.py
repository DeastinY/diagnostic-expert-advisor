"""Checks on a cohort: units, plausibility, clusters, duplicates, time, static columns.

Every check is a pure function of the cohort (and its profile) and yields :class:`Check`
records with a severity and, where DEA can repair the problem, an ``action`` that
:func:`dea.suggest.apply_suggestions` understands. The expensive parts run as one streaming
pass over the observations plus a sample, so they stay cheap for MIMIC-sized cohorts.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal

import polars as pl

from dea.core.model import ENCOUNTER_ID, TIME, VALUE, VARIABLE, Cohort, collect
from dea.io import progress
from dea.profile import CohortProfile, profile_cohort
from dea.quality.sites import detect_site_column
from dea.suggest.units import UnitReport, load_units
from dea.suggest.vocab import Concept, load_dictionary

Severity = Literal["info", "warning", "error"]
MIN_ROWS = 5  # never report a finding backed by fewer rows (k-anonymity)
OUTLIER_FRACTION = 0.002  # below this share, out-of-range values are noise, not a unit problem
# Records up to this many hours before admission are the expected run-up (emergency
# department labs); anything earlier belongs to another stay or a pre-admission visit.
EARLY_LEAD_HOURS = 24
REVIEW_MAX = 25  # up to this many out-of-range values are listed one by one for a decision
_NUMBER = re.compile(
    r"^\s*[-+]?\d+(?:[.,]\d+)?"
)  # the same leading number parse_numeric_events takes


@dataclass
class Check:
    id: str
    severity: Severity
    title: str
    detail: str
    variables: list[str] = field(default_factory=list)
    action: dict[str, Any] | None = None
    n: int = 0
    recommended: bool = False  # the action is safe to apply without looking: on by default
    # alternative ways to fix it, [{id, label, detail, action}]; ``action`` is the default one
    options: list[dict[str, Any]] | None = None
    # the data behind the finding, for display: samples, before/after values, listed rows
    examples: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def choose(checks: list[Check], ids: list[str] | str) -> list[Check]:
    """The checks whose fix was chosen, each with the chosen option as its action.

    ``ids`` is ``"recommended"`` or entries of the form ``id`` (the default action),
    ``id#option`` (one of the check's ``options``) or ``id#rows=enc:value:decision,...`` (a
    decision per listed row: ``drop``, ``clamp`` or ``keep``). The entry becomes the returned
    check's id, so what was applied is remembered with the choice.
    """
    if ids == "recommended":
        return [c for c in checks if c.action and c.recommended]
    by_id = {c.id: c for c in checks}
    out = []
    for entry in ids:
        cid, _, choice = entry.partition("#")
        c = by_id.get(cid)
        if c is None or not c.action:
            continue
        chosen = replace(c, id=entry)
        if choice.startswith("rows="):
            chosen.action = _row_actions(c, choice[5:])
        elif choice:
            option = next((o for o in c.options or [] if o["id"] == choice), None)
            if option is not None:
                chosen.action = option["action"]
            else:  # an option this check does not have: the default, remembered as such
                chosen.id = cid
        if chosen.action:
            out.append(chosen)
    return out


def _row_actions(check: Check, spec: str) -> dict[str, Any] | None:
    """Per-row decisions for a range finding: ``enc:value:drop|clamp|keep`` joined by commas."""
    fix = (check.action or {}).get("fix")
    bounds = (check.examples or {}).get("bounds")
    if not fix or not bounds:
        return None
    lo, hi = bounds
    fixes = []
    for item in spec.split(","):
        parts = item.rsplit(":", 2)
        if len(parts) != 3:
            continue
        enc, value, decision = parts
        try:
            v = float(value)
        except ValueError:
            continue
        if decision == "keep":
            continue
        expr = None if decision == "drop" else str(hi if v > hi else lo)
        fixes.append(
            {
                "variable": fix["variable"],
                "where": f"value = {v!r}",
                "expr": expr,
                "encounter_where": "encounter_id = '" + enc.replace("'", "''") + "'",
                "description": (
                    "dropped after review" if expr is None else f"clamped to {expr} after review"
                ),
            }
        )
    return {"fixes": fixes} if fixes else None


def _quote(pairs: list[tuple[str, int]]) -> str:
    return ", ".join(f"'{v}' ({n:,} rows)" for v, n in pairs)


def apply_checks(cohort: Cohort, checks: list[Check]) -> Cohort:
    """Apply the actions of the given checks (same actions as suggestions)."""
    from dea.suggest.engine import apply_suggestions

    return apply_suggestions(cohort, [c for c in checks if c.action])  # type: ignore[arg-type]


def _concepts(variables: list[str], skip: list[str] | None = None) -> dict[str, Concept]:
    d = load_dictionary()
    out = {}
    for v in variables:
        if skip and v in skip:
            continue
        m = d.match(v)
        if m:
            out[v] = m[0]
    return out


def unit_checks(reports: list[UnitReport]) -> list[Check]:
    """Checks from the import-time unit normalization."""
    out = []
    for r in reports:
        unit = r.label or r.canonical
        if r.skipped:
            out.append(
                Check(
                    id=f"units-skip:{r.variable}",
                    severity="info",
                    title=f"'{r.variable}' is not in the unit its dictionary match expects",
                    detail=(
                        "All rows are in "
                        + ", ".join(f"{u!r}" for u in r.unconvertible)
                        + f", which does not fit {unit}. It is probably a different measurement "
                        "under that name (a drug level rather than a dose, urine rather than "
                        "blood), so it was left as is and not mapped to the concept."
                    ),
                    variables=[r.variable],
                    n=sum(r.unconvertible.values()),
                )
            )
            continue
        if r.converted:
            total = sum(r.seen.values())
            n = sum(r.converted.values())
            out.append(
                Check(
                    id=f"units:{r.variable}",
                    severity="info",
                    title=f"'{r.variable}' was recorded in mixed units, now {unit}",
                    detail=(
                        f"{n:,} of {total:,} rows ({n / total:.1%}) in "
                        + ", ".join(r.converted)
                        + f" were converted to {unit}; the stored values are uniform."
                    ),
                    variables=[r.variable],
                    n=n,
                )
            )
        if r.unconvertible:
            names = ", ".join(f"'{r.split_name(r.variable, u)}'" for u in r.unconvertible)
            out.append(
                Check(
                    id=f"units-split:{r.variable}",
                    severity="warning",
                    title=f"'{r.variable}' has rows in a unit DEA cannot convert; split off",
                    detail=(
                        ", ".join(f"{n:,} rows in {u!r}" for u, n in r.unconvertible.items())
                        + f" cannot be converted to {unit} and became {names}, so the "
                        "values of one variable stay in one unit. Rename, drop or add a "
                        "conversion to units.yaml if that is the same measurement."
                    ),
                    variables=[r.variable, *(r.split_name(r.variable, u) for u in r.unconvertible)],
                    n=sum(r.unconvertible.values()),
                )
            )
    return out


def range_checks(cohort: Cohort, concepts: dict[str, Concept]) -> list[Check]:
    """Out-of-range values per dictionary concept, with a unit hypothesis when one fits.

    One streaming pass counts the rows below and above the plausible range and takes the
    median of each outlier cluster. When a known conversion moves that median into range,
    the check proposes the conversion; otherwise it proposes dropping the rows.
    """
    ranged = {v: c for v, c in concepts.items() if c.range}
    if not ranged:
        return []
    lo_expr = pl.col(VARIABLE).replace_strict(
        {v: float(c.range[0]) for v, c in ranged.items()},
        default=None,
        return_dtype=pl.Float64(),
    )
    hi_expr = pl.col(VARIABLE).replace_strict(
        {v: float(c.range[1]) for v, c in ranged.items()},
        default=None,
        return_dtype=pl.Float64(),
    )
    below, above = pl.col(VALUE) < lo_expr, pl.col(VALUE) > hi_expr
    stats = collect(
        cohort.dynamic.filter(pl.col(VARIABLE).is_in(list(ranged)))
        .group_by(VARIABLE)
        .agg(
            pl.len().alias("n"),
            below.sum().alias("n_below"),
            above.sum().alias("n_above"),
            pl.col(VALUE).filter(below).median().alias("med_below"),
            pl.col(VALUE).filter(above).median().alias("med_above"),
        )
    )
    units = load_units()
    out = []
    for row in stats.iter_rows(named=True):
        var, c = row[VARIABLE], ranged[row[VARIABLE]]
        lo, hi = c.range
        n_bad = row["n_below"] + row["n_above"]
        if row["n"] >= MIN_ROWS and n_bad / row["n"] >= 0.5:
            explained = any(
                med is not None and lo <= conv.apply(med) <= hi
                for conv in units.conversions_into(c.ucum, c.name)
                for med in (row["med_below"], row["med_above"])
            )
            if not explained:
                out.append(
                    Check(
                        id=f"concept:{var}",
                        severity="warning",
                        title=f"'{var}' does not look like {c.label}",
                        detail=(
                            f"{n_bad / row['n']:.0%} of its values are outside {lo}-{hi} "
                            f"{c.unit}, the plausible range of {c.label}, and no unit "
                            "conversion explains that. It is probably a different measurement "
                            "under a similar name. Unmapping keeps the data and forgets the "
                            "concept, its code and its range."
                        ),
                        variables=[var],
                        action={"unmap": var},
                        n=int(n_bad),
                        recommended=True,
                    )
                )
                continue
        for side, n_out, med in (
            ("above", row["n_above"], row["med_above"]),
            ("below", row["n_below"], row["med_below"]),
        ):
            if n_out < MIN_ROWS or n_out / row["n"] < OUTLIER_FRACTION or med is None:
                continue
            where = f"value > {hi}" if side == "above" else f"value < {lo}"
            share = n_out / row["n"]
            values = collect(
                cohort.dynamic.filter(pl.col(VARIABLE) == var).select(ENCOUNTER_ID, TIME, VALUE)
            )
            hit = (pl.col(VALUE) > hi) if side == "above" else (pl.col(VALUE) < lo)
            outside, inside = values.filter(hit), values.filter(~hit)
            sample_out = sorted(outside.get_column(VALUE).unique().to_list())[:5]
            sample_in = sorted(inside.get_column(VALUE).unique().to_list())[:5]
            stays = {
                "n": int(inside.height),
                "sample": [float(v) for v in sample_in],
                "median": float(inside.get_column(VALUE).median()) if inside.height else None,
            }
            hypothesis = None
            for conv in units.conversions_into(c.ucum, c.name):
                if lo <= conv.apply(med) <= hi:
                    hypothesis = conv
                    break
            if hypothesis is not None:
                expr = f"value * {hypothesis.factor:.10g}"
                if hypothesis.offset:
                    expr += f" + {hypothesis.offset:.10g}"
                out.append(
                    Check(
                        id=f"range:{var}",
                        severity="warning",
                        title=f"{share:.1%} of '{var}' look like {hypothesis.src}, not {c.ucum}",
                        detail=(
                            f"{n_out:,} values {side} the plausible range {lo}-{hi} {c.unit} "
                            f"(median {med:.3g}) fit {c.label} in {hypothesis.src}. Converting "
                            f"them gives a median of {hypothesis.apply(med):.3g} {c.unit}. "
                            + (
                                f"The other {stays['n']:,} value{'s' if stays['n'] != 1 else ''}"
                                + (
                                    f" (median {stays['median']:.3g})"
                                    if stays["median"] is not None and stays["n"] > 1
                                    else f" ({stays['median']:.3g})"
                                    if stays["median"] is not None
                                    else ""
                                )
                                + f" {'are' if stays['n'] != 1 else 'is'} already in range and "
                                + f"stay{'' if stays['n'] != 1 else 's'} as "
                                + f"{'they are' if stays['n'] != 1 else 'it is'}."
                                if stays["n"]
                                else "Every value is affected."
                            )
                        ),
                        variables=[var],
                        action={
                            "fix": {
                                "variable": var,
                                "where": where,
                                "expr": expr,
                                "description": f"{hypothesis.src} -> {c.ucum}",
                            }
                        },
                        n=int(n_out),
                        recommended=True,
                        examples={
                            "bounds": [lo, hi],
                            "side": side,
                            "before": [float(v) for v in sample_out],
                            "after": [float(hypothesis.apply(v)) for v in sample_out],
                            "stats": {
                                "before": float(med),
                                "after": float(hypothesis.apply(med)),
                            },
                            "unchanged": stays,
                        },
                    )
                )
            else:
                bound = hi if side == "above" else lo
                drop_action = {
                    "fix": {
                        "variable": var,
                        "where": where,
                        "expr": None,
                        "description": f"implausible values {side} {lo}-{hi} dropped",
                    }
                }
                out.append(
                    Check(
                        id=f"range:{var}:{side}",
                        severity="warning" if share >= 0.01 else "info",
                        title=f"{share:.1%} of '{var}' are {side} the plausible range",
                        detail=(
                            f"{n_out:,} values {side} {lo}-{hi} {c.unit} for {c.label} "
                            f"(median {med:.3g}). No unit conversion explains them; they are "
                            "probably entry errors or a different measurement. Drop them, "
                            "clamp them to the range, or leave them"
                            + (" and decide one by one below." if n_out <= REVIEW_MAX else ".")
                        ),
                        variables=[var],
                        action=drop_action,
                        options=[
                            {
                                "id": "drop",
                                "label": "drop them",
                                "detail": "the rows disappear",
                                "action": drop_action,
                            },
                            {
                                "id": "clamp",
                                "label": f"clamp them to {bound:g}",
                                "detail": "the rows stay, at the edge of the plausible range",
                                "action": {
                                    "fix": {
                                        "variable": var,
                                        "where": where,
                                        "expr": str(bound),
                                        "description": f"values {side} {lo}-{hi} clamped",
                                    }
                                },
                            },
                        ],
                        n=int(n_out),
                        examples={
                            "bounds": [lo, hi],
                            "side": side,
                            "before": [float(v) for v in sample_out],
                            "unchanged": stays,
                            "rows": (
                                [
                                    {
                                        "encounter": e,
                                        "hours": round(t.total_seconds() / 3600, 2),
                                        "value": float(v),
                                    }
                                    for e, t, v in outside.sort(VALUE, descending=side == "above")
                                    .head(REVIEW_MAX)
                                    .iter_rows()
                                ]
                                if n_out <= REVIEW_MAX
                                else None
                            ),
                        },
                    )
                )
    return out


def cluster_checks(cohort: Cohort, skip: set[str], bins: int = 24) -> list[Check]:
    """Two separated clusters in a variable's distribution (a unit mix the ranges missed)."""
    hist = cohort.distribution_bins(bins=bins)
    out = []
    for (var,), part in hist.group_by(VARIABLE, maintain_order=True):
        if var in skip or part.height < bins:
            continue
        counts = part.get_column("count").to_list()
        total = sum(counts)
        if total < 200 or len(set(counts)) <= 2:
            continue
        peak = max(range(bins), key=counts.__getitem__)
        # second peak: highest bin at least 3 bins away with an (almost) empty valley between;
        # the 2% bar keeps integer-valued or clipped signals from looking bimodal
        best = None
        for j in range(bins):
            if abs(j - peak) < 3 or counts[j] < 0.15 * counts[peak]:
                continue
            lo_i, hi_i = sorted((peak, j))
            valley = min(counts[lo_i + 1 : hi_i])
            if valley <= 0.02 * min(counts[peak], counts[j]) and (
                best is None or counts[j] > counts[best]
            ):
                best = j
        if best is None:
            continue
        rows = part.to_dicts()
        a, b = rows[peak], rows[best]
        minority = min(counts[peak], counts[best]) / total
        out.append(
            Check(
                id=f"cluster:{var}",
                severity="warning",
                title=f"'{var}' has two separate value clusters",
                detail=(
                    f"Values pile up around {(a['lo'] + a['hi']) / 2:.3g} and "
                    f"{(b['lo'] + b['hi']) / 2:.3g} with nothing in between "
                    f"(smaller cluster {minority:.0%} of a sample). Two units, two devices or "
                    "two different measurements share this name."
                ),
                variables=[var],
                n=int(min(counts[peak], counts[best])),
            )
        )
    return out


def duplicate_checks(cohort: Cohort) -> list[Check]:
    """Repeated (encounter, time, variable) keys, told apart by whether the value repeats too.

    Exact repeats (same value) carry no information and are safe to drop. Keys with *different*
    values are a second lab run, a correction or two source items merged under one name; those
    are listed with their values and resolved only by an explicit choice.
    """
    key = [ENCOUNTER_ID, TIME, VARIABLE]
    dyn = cohort.dynamic
    groups = collect(
        dyn.group_by(key)
        .agg(pl.len().alias("k"), pl.col(VALUE).n_unique().alias("nv"))
        .filter(pl.col("k") > 1)
    )
    out: list[Check] = []
    if not groups.height:
        return out
    exact = int((groups["k"] - groups["nv"]).sum())
    if exact:
        top = (
            groups.with_columns((pl.col("k") - pl.col("nv")).alias("extra"))
            .group_by(VARIABLE)
            .agg(pl.col("extra").sum())
            .filter(pl.col("extra") > 0)
            .sort(["extra", VARIABLE], descending=[True, False])
            .head(6)
        )
        listed = ", ".join(f"'{v}' ({n:,})" for v, n in top.iter_rows())
        out.append(
            Check(
                id="duplicates",
                severity="warning",
                title=f"{exact:,} exact duplicate observation(s)",
                detail=(
                    "The same encounter, time, variable and value recorded more than once, "
                    f"mostly {listed}. Exact repeats carry no information; one copy is kept."
                ),
                action={"dedupe": "exact"},
                n=exact,
                recommended=True,
                examples={"top": [{"variable": v, "n": int(n)} for v, n in top.iter_rows()]},
            )
        )
    conflicting = groups.filter(pl.col("nv") > 1)
    if conflicting.height:
        n_rows = int((conflicting["nv"] - 1).sum())
        sample = conflicting.sort([VARIABLE, ENCOUNTER_ID, TIME]).head(8).select(key)
        rows = collect(
            dyn.join(sample.lazy(), on=key, how="inner")
            .group_by(key)
            .agg(pl.col(VALUE).alias("values"))
            .sort([VARIABLE, ENCOUNTER_ID, TIME])
        )
        options = [
            {
                "id": "first",
                "label": "keep the first value",
                "detail": "the row that came first in the source",
                "action": {"dedupe": "first"},
            },
            {
                "id": "last",
                "label": "keep the last value",
                "detail": "a later entry is usually the correction",
                "action": {"dedupe": "last"},
            },
            {
                "id": "mean",
                "label": "keep the mean",
                "detail": "one averaged observation per timestamp",
                "action": {"dedupe": "mean"},
            },
            {
                "id": "drop",
                "label": "drop all of them",
                "detail": "no value at those timestamps",
                "action": {"dedupe": "drop"},
            },
        ]
        out.append(
            Check(
                id="duplicates:conflicting",
                severity="warning",
                title=f"{n_rows:,} conflicting value(s) at the same time",
                detail=(
                    f"{conflicting.height:,} timestamps where the same variable was recorded "
                    "with different values: two lab runs, a correction, or two source items "
                    "merged under one name. These are not duplicates; nothing happens until "
                    "you pick how to resolve them."
                ),
                action=options[0]["action"],
                options=options,
                n=n_rows,
                examples={
                    "rows": [
                        {
                            "encounter": e,
                            "hours": round(t.total_seconds() / 3600, 2),
                            "variable": v,
                            "values": [float(x) for x in vals],
                        }
                        for e, t, v, vals in rows.iter_rows()
                    ]
                },
            )
        )
    return out


def structural_checks(cohort: Cohort, profile: CohortProfile) -> list[Check]:
    out = []
    n_enc = len(cohort)
    dyn = cohort.dynamic
    lead = cohort.config.processing.pre_admission_lead_hours
    counts = collect(
        dyn.select(
            pl.len().alias("n"),
            (pl.col(TIME) < pl.duration(seconds=0)).sum().alias("negative"),
            (pl.col(TIME) < pl.duration(hours=-lead)).sum().alias("early"),
            pl.col(ENCOUNTER_ID)
            .filter(pl.col(TIME) < pl.duration(seconds=0))
            .n_unique()
            .alias("enc_negative"),
            pl.col(TIME).min().alias("earliest"),
            (pl.col(TIME) > pl.duration(days=90)).sum().alias("late"),
            pl.col(VALUE).is_nan().sum().alias("nan"),
        )
    ).row(0, named=True)
    out += duplicate_checks(cohort)
    if counts["early"]:
        earliest = -counts["earliest"].total_seconds() / 3600
        out.append(
            Check(
                id="time:early",
                severity="warning",
                title=(f"{counts['early']:,} observation(s) more than {lead:g} h before admission"),
                detail=(
                    f"The earliest is {earliest:,.0f} h before admission. Records that far back "
                    "are not part of this stay: a weight or height from an earlier hospital "
                    "admission, alarm limits of a previous ICU stay, or a wrong admission time. "
                    "They distort the first hours of every chart and feature, so dropping them "
                    f"is the safe default. The {lead:g} h run-up that counts as part of the "
                    "stay can be changed under Processing."
                ),
                action={"drop_time": "early"},
                n=int(counts["early"]),
                recommended=True,
            )
        )
    shortly = int(counts["negative"] - counts["early"])
    if shortly:
        options = [
            {
                "id": "drop",
                "label": "drop them",
                "detail": "the stay starts exactly at admission",
                "action": {"drop_time": "negative"},
            },
            {
                "id": "clamp",
                "label": "count them as admission values",
                "detail": "their time becomes hour 0, so features and trajectories see them",
                "action": {"drop_time": "clamp"},
            },
        ]
        out.append(
            Check(
                id="time:negative",
                severity="info",
                title=(f"{shortly:,} observation(s) in the {lead:g} h before admission"),
                detail=(
                    f"In {int(counts['enc_negative']):,} of {n_enc:,} encounters some data "
                    "precedes the admission time: labs drawn in the emergency department, a "
                    "weight taken on the ward. That run-up is expected and kept as it is, at "
                    "negative hours, which charts that start at admission leave out. Drop it if "
                    "your question starts exactly at admission, or count it as admission "
                    f"values. The {lead:g} h window can be changed under Processing."
                ),
                action=options[0]["action"],
                options=options,
                n=shortly,
            )
        )
    if counts["late"]:
        out.append(
            Check(
                id="time:late",
                severity="warning",
                title=f"{counts['late']:,} observation(s) more than 90 days after admission",
                detail="Very long offsets usually mean records of a later stay were attached "
                "to this encounter, or a wrong time unit.",
                n=int(counts["late"]),
            )
        )
    if counts["nan"]:
        out.append(
            Check(
                id="values:nan",
                severity="info",
                title=f"{counts['nan']:,} NaN value(s)",
                detail="Not-a-number values are ignored by aggregates but count as observations.",
                n=int(counts["nan"]),
            )
        )
    empty = cohort.features().filter(pl.col("n_observations") == 0).height
    if empty:
        out.append(
            Check(
                id="encounters:empty",
                severity="warning",
                title=f"{empty} encounter(s) without observations",
                detail="They appear in the static table only.",
                action={
                    "filter": {
                        "label": "Has observations",
                        "where": "n_observations > 0",
                        "description": "",
                    }
                },
                n=empty,
            )
        )
    for s in profile.static_columns:
        if n_enc and s.n_missing / n_enc > 0.3 and s.n_missing >= MIN_ROWS:
            out.append(
                Check(
                    id=f"static-missing:{s.name}",
                    severity="info",
                    title=f"'{s.name}' missing for {s.n_missing / n_enc:.0%} of encounters",
                    detail="Filters on this column silently exclude encounters without a value.",
                    variables=[s.name],
                    n=s.n_missing,
                )
            )
        if s.name == "age" and s.kind == "numeric" and s.max is not None and s.max > 120:
            out.append(
                Check(
                    id="static:age",
                    severity="warning",
                    title=f"Ages up to {s.max:.0f}",
                    detail="Ages above 120 are placeholders (MIMIC shifts ages over 89 to 91) "
                    "or errors.",
                    variables=["age"],
                )
            )
    low = [v.name for v in profile.variables if "low_coverage" in v.flags]
    if low:
        out.append(
            Check(
                id="coverage:low",
                severity="info",
                title=f"{len(low)} variable(s) present in fewer than 10% of encounters",
                detail=", ".join(low[:15])
                + (" …" if len(low) > 15 else "")
                + ". They can be hidden from features and charts (the data stays).",
                variables=low,
                action={"drop": low},
                n=len(low),
            )
        )
    return out


def event_checks(cohort: Cohort, profile: CohortProfile) -> list[Check]:
    """String-valued variables whose values are really numbers with a unit glued on."""
    out = []
    for e in profile.events:
        if e.n_observations < MIN_ROWS or not e.top_values:
            continue
        numeric_like = sum(
            n for v, n in e.top_values if v.strip().split(" ")[0].replace(".", "", 1).isdigit()
        )
        shown = sum(n for _, n in e.top_values)
        if shown and numeric_like / shown >= 0.8:
            counts = collect(
                cohort.events.filter(pl.col(VARIABLE) == e.name)
                .group_by(pl.col(VALUE).cast(pl.String()))
                .agg(pl.len().alias("n"))
                .sort(["n", VALUE], descending=[True, False])
            )
            pairs = [(str(v), int(n)) for v, n in counts.iter_rows()]
            parsed_top = [(v, n) for v, n in pairs if _NUMBER.match(v)]
            kept_top = [(v, n) for v, n in pairs if not _NUMBER.match(v)]
            n_parsed = sum(n for _, n in parsed_top)
            n_kept = sum(n for _, n in kept_top)
            detail = (
                f"Values such as {_quote(parsed_top[:3])} are numbers with text glued on; as "
                f"events they take no part in aggregates. The leading number of {n_parsed:,} "
                "rows becomes an observation"
            )
            if n_kept:
                detail += f"; {n_kept:,} rows without one"
                if kept_top:
                    detail += f", such as {_quote(kept_top[:2])},"
                detail += " stay events."
            else:
                detail += "."
            out.append(
                Check(
                    id=f"events-numeric:{e.name}",
                    severity="warning",
                    title=f"'{e.name}' holds numbers stored as text",
                    detail=detail,
                    variables=[e.name],
                    action={"parse": {"variable": e.name}},
                    n=n_parsed,
                    recommended=True,
                    examples={
                        "parsed": [{"value": v, "n": n} for v, n in parsed_top[:6]],
                        "kept": [{"value": v, "n": n} for v, n in kept_top[:6]],
                        "n_parsed": n_parsed,
                        "n_kept": n_kept,
                    },
                )
            )
    return out


def run_checks(
    cohort: Cohort,
    profile: CohortProfile | None = None,
    unit_reports: list[UnitReport] | None = None,
) -> list[Check]:
    """All checks, most severe first."""
    progress.report("checks", "profiling the variables")
    profile = profile or profile_cohort(cohort)
    concepts = _concepts([v.name for v in profile.variables], skip=cohort.config.unmapped)
    out = unit_checks(unit_reports or [])
    progress.report("checks", "plausible ranges")
    ranged = range_checks(cohort, concepts)
    out += ranged
    flagged = {v for c in out for v in c.variables}
    progress.report("checks", "distributions")
    out += cluster_checks(cohort, skip=flagged)
    progress.report("checks", "duplicates and timing")
    out += structural_checks(cohort, profile)
    out += event_checks(cohort, profile)
    site_col = cohort.config.site_column or detect_site_column(cohort.static)
    if site_col and site_col in cohort.static.columns:
        from dea.quality.sites import site_checks

        out += site_checks(cohort, site_col)
    order = {"error": 0, "warning": 1, "info": 2}
    seen: set[str] = set()
    unique = [c for c in out if not (c.id in seen or seen.add(c.id))]  # type: ignore[func-returns-value]
    return sorted(unique, key=lambda c: (order[c.severity], -c.n))


__all__ = ["Check", "apply_checks", "run_checks", "unit_checks", "range_checks", "cluster_checks"]

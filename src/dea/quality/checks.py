"""Checks on a cohort: units, plausibility, clusters, duplicates, time, static columns.

Every check is a pure function of the cohort (and its profile) and yields :class:`Check`
records with a severity and, where DEA can repair the problem, an ``action`` that
:func:`dea.suggest.apply_suggestions` understands. The expensive parts run as one streaming
pass over the observations plus a sample, so they stay cheap for MIMIC-sized cohorts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
                            f"them gives a median of {hypothesis.apply(med):.3g} {c.unit}."
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
                    )
                )
            else:
                out.append(
                    Check(
                        id=f"range:{var}:{side}",
                        severity="warning" if share >= 0.01 else "info",
                        title=f"{share:.1%} of '{var}' are {side} the plausible range",
                        detail=(
                            f"{n_out:,} values {side} {lo}-{hi} {c.unit} for {c.label} "
                            f"(median {med:.3g}). No unit conversion explains them; they are "
                            "probably entry errors or a different measurement."
                        ),
                        variables=[var],
                        action={
                            "fix": {
                                "variable": var,
                                "where": where,
                                "expr": None,
                                "description": f"implausible values {side} {lo}-{hi} dropped",
                            }
                        },
                        n=int(n_out),
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


def structural_checks(cohort: Cohort, profile: CohortProfile) -> list[Check]:
    out = []
    n_enc = len(cohort)
    dyn = cohort.dynamic
    counts = collect(
        dyn.select(
            pl.len().alias("n"),
            (pl.col(TIME) < pl.duration(seconds=0)).sum().alias("negative"),
            (pl.col(TIME) < pl.duration(hours=-EARLY_LEAD_HOURS)).sum().alias("early"),
            pl.col(TIME).min().alias("earliest"),
            (pl.col(TIME) > pl.duration(days=90)).sum().alias("late"),
            pl.col(VALUE).is_nan().sum().alias("nan"),
        )
    ).row(0, named=True)
    dup = int(
        collect(
            dyn.group_by(ENCOUNTER_ID, TIME, VARIABLE)
            .agg(pl.len().alias("k"))
            .select((pl.col("k") - 1).sum())
        ).item()
    )
    if dup:
        out.append(
            Check(
                id="duplicates",
                severity="warning",
                title=f"{dup:,} duplicate observation(s)",
                detail="Same encounter, time and variable recorded more than once; the first "
                "value is kept when deduplicating.",
                action={"dedupe": True},
                n=int(dup),
                recommended=True,
            )
        )
    if counts["early"]:
        earliest = -counts["earliest"].total_seconds() / 3600
        out.append(
            Check(
                id="time:early",
                severity="warning",
                title=(
                    f"{counts['early']:,} observation(s) more than {EARLY_LEAD_HOURS} h "
                    "before admission"
                ),
                detail=(
                    f"The earliest is {earliest:,.0f} h before admission. Records that far back "
                    "are not part of this stay: a weight or height from an earlier hospital "
                    "admission, alarm limits of a previous ICU stay, or a wrong admission time. "
                    "They distort the first hours of every chart and feature, so dropping them "
                    "is the safe default."
                ),
                action={"drop_time": "early"},
                n=int(counts["early"]),
                recommended=True,
            )
        )
    shortly = int(counts["negative"] - counts["early"])
    if shortly:
        out.append(
            Check(
                id="time:negative",
                severity="info",
                title=(f"{shortly:,} observation(s) in the {EARLY_LEAD_HOURS} h before admission"),
                detail="A short run-up before the admission time is expected: labs drawn in "
                "the emergency department belong to the stay and are kept by default. Drop "
                "them only if your question starts exactly at admission.",
                action={"drop_time": "negative"},
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
            out.append(
                Check(
                    id=f"events-numeric:{e.name}",
                    severity="warning",
                    title=f"'{e.name}' holds numbers stored as text",
                    detail="Most values look like '37.2 C' or '98'; as events they do not take "
                    "part in aggregates. The leading number can be parsed into an observation; "
                    "rows without one stay events.",
                    variables=[e.name],
                    action={"parse": {"variable": e.name}},
                    n=e.n_observations,
                    recommended=True,
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

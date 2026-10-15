"""Compare a cohort across the sites, hospitals or exports it was pooled from.

A pooled cohort hides site-specific units, devices, sampling grids and missing variables
inside one distribution. Given a static column that names the site, :func:`site_profile`
summarizes every variable per site from a stratified sample, :func:`site_bins` gives
comparable histograms, and :func:`site_checks` turns the differences that matter into
findings: a variable missing at one site, a shift that a unit conversion explains (with a
site-restricted fix), a plain distribution shift, a coarser sampling grid, a static column
one site never exports.
"""

from __future__ import annotations

import re

import polars as pl

from dea.core.model import ENCOUNTER_ID, TIME, VALUE, VARIABLE, Cohort, collect
from dea.suggest.units import load_units
from dea.suggest.vocab import load_dictionary

MIN_SITE_ENCOUNTERS = 5
_SITE_HINT = re.compile(
    r"(site|hospital|cent(er|re)|clinic|institution|facility|provider|organi[sz]ation"
    r"|klinik|standort)",
    re.I,
)


def detect_site_column(static: pl.DataFrame) -> str | None:
    """A static column that looks like a site identifier: hinted name, 2-50 categories."""
    for name in static.columns:
        if name == ENCOUNTER_ID or not _SITE_HINT.search(name):
            continue
        col = static.get_column(name)
        if col.dtype in (pl.String, pl.Categorical, pl.Enum) or col.dtype.is_integer():
            n = col.drop_nulls().n_unique()
            if 2 <= n <= 50:
                return name
    return None


def _sampled(cohort: Cohort, site_col: str, sample: int) -> tuple[pl.DataFrame, pl.LazyFrame]:
    """(sampled static rows with the site, sampled observations joined with the site)."""
    static = (
        cohort.static.select(ENCOUNTER_ID, pl.col(site_col).cast(pl.String()).alias("site"))
        .drop_nulls("site")
        .sort(ENCOUNTER_ID)
    )
    picked = static.with_columns(
        (pl.int_range(pl.len()).over("site") * sample // pl.len().over("site")).alias("_slot")
    )
    picked = picked.unique(subset=["site", "_slot"], keep="first").drop("_slot")
    lf = cohort.dynamic.join(picked.lazy(), on=ENCOUNTER_ID, how="inner")
    return picked, lf


def site_profile(cohort: Cohort, site_col: str, sample: int = 200) -> pl.DataFrame:
    """Per site and variable: encounters, coverage, observations, quartiles, sampling interval.

    Quartiles are of the per-encounter medians. Up to ``sample`` encounters per site are
    used, so the cost does not grow with the cohort.
    """
    picked, lf = _sampled(cohort, site_col, sample)
    per_site = picked.group_by("site").agg(pl.len().alias("n_site"))
    # quartiles over per-encounter medians: one value per stay, so a long stay does not
    # dominate and within-stay variation does not hide a systematic site effect
    stats = collect(
        lf.group_by("site", VARIABLE, ENCOUNTER_ID)
        .agg(pl.len().alias("_n"), pl.col(VALUE).median().alias("_m"))
        .group_by("site", VARIABLE)
        .agg(
            pl.len().alias("n_encounters"),
            pl.col("_n").sum().alias("n_obs"),
            pl.col("_m").quantile(0.25).alias("p25"),
            pl.col("_m").median().alias("median"),
            pl.col("_m").quantile(0.75).alias("p75"),
        )
    )
    intervals = collect(
        lf.group_by("site", VARIABLE, ENCOUNTER_ID)
        .agg(
            # gaps between distinct time stamps; a lab panel at one time is not an interval
            pl.col(TIME).unique().sort().diff().dt.total_seconds().median().alias("_iv")
        )
        .group_by("site", VARIABLE)
        .agg(pl.col("_iv").median().alias("interval_s"))
    )
    return (
        stats.join(intervals, on=["site", VARIABLE], how="left")
        .join(per_site, on="site", how="left")
        .with_columns((pl.col("n_encounters") / pl.col("n_site")).alias("coverage"))
        .sort(VARIABLE, "site")
    )


def site_bins(
    cohort: Cohort,
    site_col: str,
    variables: list[str] | None = None,
    bins: int = 24,
    sample: int = 200,
) -> pl.DataFrame:
    """Histograms per variable and site on shared bins, normalized per site (``fraction``)."""
    _, lf = _sampled(cohort, site_col, sample)
    if variables is not None:
        lf = lf.filter(pl.col(VARIABLE).is_in(list(variables)))
    values = collect(lf.select("site", VARIABLE, VALUE))
    schema = {
        VARIABLE: pl.String(),
        "site": pl.String(),
        "lo": pl.Float64(),
        "hi": pl.Float64(),
        "count": pl.UInt32(),
        "fraction": pl.Float64(),
    }
    if values.is_empty():
        return pl.DataFrame(schema=schema)
    bounds = (
        values.group_by(VARIABLE)
        .agg(pl.col(VALUE).quantile(0.01).alias("_lo"), pl.col(VALUE).quantile(0.99).alias("_hi"))
        .with_columns(
            pl.when(pl.col("_hi") > pl.col("_lo"))
            .then(pl.col("_hi") - pl.col("_lo"))
            .otherwise(1.0)
            .alias("_w")
        )
    )
    return (
        values.join(bounds, on=VARIABLE)
        .with_columns(
            ((pl.col(VALUE) - pl.col("_lo")) / pl.col("_w") * bins)
            .floor()
            .clip(0, bins - 1)
            .cast(pl.Int64())
            .alias("_b")
        )
        .group_by(VARIABLE, "site", "_b", "_lo", "_w")
        .agg(pl.len().alias("count"))
        .with_columns(
            (pl.col("_lo") + pl.col("_b") * pl.col("_w") / bins).alias("lo"),
            (pl.col("_lo") + (pl.col("_b") + 1) * pl.col("_w") / bins).alias("hi"),
            (pl.col("count") / pl.col("count").sum().over(VARIABLE, "site")).alias("fraction"),
        )
        .select(list(schema))
        .sort(VARIABLE, "site", "lo")
    )


def _names(sites: list[str], limit: int = 4) -> str:
    shown = ", ".join(str(x) for x in sites[:limit])
    return shown + (f" and {len(sites) - limit} more" if len(sites) > limit else "")


def site_checks(cohort: Cohort, site_col: str, profile: pl.DataFrame | None = None) -> list:
    """Findings from comparing sites; see the module docstring."""
    from dea.quality.checks import Check

    profile = profile if profile is not None else site_profile(cohort, site_col)
    out: list[Check] = []
    sizes = cohort.static.group_by(pl.col(site_col).cast(pl.String()).alias("site")).agg(
        pl.len().alias("n")
    )
    small = sizes.filter(pl.col("n") < MIN_SITE_ENCOUNTERS).get_column("site").to_list()
    big = [s for s in sizes.get_column("site").to_list() if s is not None and s not in small]
    if small:
        out.append(
            Check(
                id="site:small",
                severity="info",
                title=f"{len(small)} site(s) with fewer than {MIN_SITE_ENCOUNTERS} encounters",
                detail=", ".join(map(str, small)) + "; they are left out of the comparisons.",
                n=len(small),
            )
        )
    if len(big) < 2:
        return out
    profile = profile.filter(pl.col("site").is_in(big))
    ref_by_var = (
        profile.sort("n_encounters", descending=True)
        .unique(subset=[VARIABLE], keep="first")
        .select(VARIABLE, pl.col("site").alias("_ref"))
    )
    d, units = load_dictionary(), load_units()
    label = cohort.config.label
    n_big = len(big)
    for (var,), part in profile.group_by(VARIABLE, maintain_order=True):
        rows = {r["site"]: r for r in part.iter_rows(named=True)}
        present = {s: 0.0 for s in big}
        present.update({s: r["coverage"] for s, r in rows.items() if s in present})
        have = sorted(s for s, c in present.items() if c >= 0.5)
        missing = sorted(s for s, c in present.items() if c < 0.1)
        if have and missing and len(have) >= max(2, n_big // 4):
            out.append(
                Check(
                    id=f"site:coverage:{var}",
                    severity="warning",
                    title=f"'{label(var)}' is recorded at {len(have)} of {n_big} sites",
                    detail=(
                        f"Present at {_names(have)} but absent at {_names(missing)}. Filters and "
                        "models using it silently exclude those sites."
                    ),
                    variables=[var],
                    n=len(missing),
                )
            )
        ref = ref_by_var.filter(pl.col(VARIABLE) == var).get_column("_ref").item()
        base = rows[ref]
        iqr = max((r["p75"] - r["p25"]) for r in rows.values() if r["p75"] is not None) or 0.0
        if base["median"] is None or iqr <= 0:
            continue
        match = d.match(var)
        concept = match[0] if match else None
        shifted: list[tuple[str, float]] = []
        for site, r in rows.items():
            if (
                site == ref
                or site not in present
                or r["median"] is None
                or r["n_encounters"] < MIN_SITE_ENCOUNTERS
            ):
                continue
            shift = abs(r["median"] - base["median"])
            if shift < 0.5 * iqr:
                continue
            hypothesis = None
            if concept and concept.ucum and shift >= 1.5 * iqr:
                for conv in units.conversions_into(concept.ucum, concept.name):
                    # the whole site distribution, not only its median, has to land on the
                    # reference after conversion (a paediatric site is not an inches site)
                    lo, hi = base["p25"] - 0.5 * iqr, base["p75"] + 0.5 * iqr
                    if all(lo <= conv.apply(r[q]) <= hi for q in ("p25", "median", "p75")):
                        hypothesis = conv
                        break
            if hypothesis is None:
                shifted.append((site, r["median"]))
                continue
            expr = f"value * {hypothesis.factor:.10g}"
            if hypothesis.offset:
                expr += f" + {hypothesis.offset:.10g}"
            site_sql = site.replace("'", "''")
            out.append(
                Check(
                    id=f"site:unit:{var}:{site}",
                    severity="warning",
                    title=f"'{label(var)}' at {site} looks like {hypothesis.src}",
                    detail=(
                        f"Median {r['median']:.3g} at {site} versus {base['median']:.3g} at "
                        f"{ref}; converting from {hypothesis.src} to {concept.ucum} gives "
                        f"{hypothesis.apply(r['median']):.3g}. Other sites are left alone."
                    ),
                    variables=[var],
                    action={
                        "fix": {
                            "variable": var,
                            "where": "value IS NOT NULL",
                            "expr": expr,
                            "encounter_where": f"{site_col} = '{site_sql}'",
                            "description": f"{site}: {hypothesis.src} -> {concept.ucum}",
                        }
                    },
                    n=int(r["n_obs"]),
                )
            )
        if shifted:
            worst = max(abs(m - base["median"]) for _, m in shifted)
            shifted.sort(key=lambda sm: -abs(sm[1] - base["median"]))
            out.append(
                Check(
                    id=f"site:shift:{var}",
                    severity="warning" if worst >= iqr else "info",
                    title=(
                        f"'{label(var)}' differs between sites"
                        if len(shifted) > 1
                        else f"'{label(var)}' is shifted at {shifted[0][0]}"
                    ),
                    detail=(
                        f"Median {base['median']:.3g} at {ref} (reference, most encounters) but "
                        + ", ".join(f"{m:.3g} at {s}" for s, m in shifted[:5])
                        + (f" and {len(shifted) - 5} more" if len(shifted) > 5 else "")
                        + f"; interquartile range {iqr:.3g}. No unit conversion explains it: "
                        "a different device, population or definition."
                    ),
                    variables=[var],
                    n=len(shifted),
                )
            )
        ivs = {s: r["interval_s"] for s, r in rows.items() if r["interval_s"] and r["n_obs"] >= 50}
        if len(ivs) >= 2 and max(ivs.values()) >= 4 * min(ivs.values()):
            coarse = max(ivs, key=ivs.get)
            fine = min(ivs, key=ivs.get)
            out.append(
                Check(
                    id=f"site:sampling:{var}",
                    severity="info",
                    title=(
                        f"'{label(var)}' is sampled {ivs[coarse] / ivs[fine]:.0f}× coarser "
                        f"at {coarse}"
                    ),
                    detail=(
                        f"Typical interval {ivs[fine] / 60:.0f} min at {fine} but "
                        f"{ivs[coarse] / 60:.0f} min at {coarse}; aggregates and per-hour features "
                        "are not comparable without resampling."
                    ),
                    variables=[var],
                    n=int(rows[coarse]["n_obs"]),
                )
            )
    # static columns one site never fills
    static = cohort.static.filter(pl.col(site_col).cast(pl.String()).is_in(big))
    for col in static.columns:
        if col in (ENCOUNTER_ID, site_col):
            continue
        nulls = (
            static.group_by(pl.col(site_col).cast(pl.String()).alias("site"))
            .agg(pl.col(col).is_null().mean().alias("f"))
            .to_dict(as_series=False)
        )
        frac = dict(zip(nulls["site"], nulls["f"], strict=True))
        empty = [s for s, f in frac.items() if f >= 0.9]
        filled = [s for s, f in frac.items() if f <= 0.1]
        if empty and filled:
            out.append(
                Check(
                    id=f"site:static:{col}",
                    severity="info",
                    title=f"'{col}' is not exported by {_names(empty)}",
                    detail="Filled at " + _names(filled) + " but empty there.",
                    variables=[col],
                    n=len(empty),
                )
            )
    return out


__all__ = ["detect_site_column", "site_bins", "site_checks", "site_profile"]

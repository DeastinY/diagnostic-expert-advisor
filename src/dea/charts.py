"""Altair chart builders with one consistent, colorblind-safe look, light and dark.

Rules applied throughout: one hue for single-series magnitude, a fixed categorical order for
identity (never cycled), sequential one-hue ramps for magnitude grids, no dual axes (different
scales become stacked panels), thin marks with rounded data-ends, recessive grid, selective
direct labels, and a hover layer on everything. ``dark=True`` re-steps the palette for a dark
surface instead of inverting it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import altair as alt
import polars as pl

from dea.core import TIME, VALUE, VARIABLE, Encounter
from dea.core.config import CohortConfig
from dea.core.model import END, START
from dea.suggest.vocab import match_variable

# Panel order on the patient page: what a clinician looks at first. Dictionary roles not listed
# (demographics) rank after these, then names the dictionary does not know, then constants.
PANEL_ROLES = (
    "vital_sign",
    "blood_gas",
    "ventilation",
    "laboratory",
    "medication",
    "fluid_balance",
    "score",
)

# Charts are sized by the code that builds them (top-N variables, bins, samples), not by
# Altair's 5,000-row guard, which a real ICU cohort trips on the first page.
alt.data_transformers.disable_max_rows()

SERIES_LIGHT = [
    "#2a78d6",
    "#eb6834",
    "#1baf7a",
    "#eda100",
    "#e87ba4",
    "#008300",
    "#6250d6",
    "#e34948",
]
SERIES_DARK = [
    "#3987e5",
    "#d95926",
    "#199e70",
    "#c98500",
    "#d55181",
    "#008300",
    "#9085e9",
    "#e66767",
]
SEQ_LIGHT = ["#e8f1fc", "#b7d3f6", "#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#0d366b"]
SEQ_DARK = ["#16233a", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"]
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


@dataclass(frozen=True)
class Theme:
    dark: bool
    series: Sequence[str]
    sequential: Sequence[str]
    surface: str
    ink: str
    ink2: str
    muted: str
    grid: str
    axis: str

    @property
    def accent(self) -> str:
        return self.series[0]


def theme(dark: bool = False) -> Theme:
    if dark:
        return Theme(
            True,
            SERIES_DARK,
            SEQ_DARK,
            "#1a1a19",
            "#f0efec",
            "#c3c2b7",
            "#898781",
            "#2c2c2a",
            "#383835",
        )
    return Theme(
        False,
        SERIES_LIGHT,
        SEQ_LIGHT,
        "#fcfcfb",
        "#0b0b0b",
        "#52514e",
        "#898781",
        "#e1e0d9",
        "#c3c2b7",
    )


def _apply(chart, t: Theme):  # noqa: ANN001, ANN202 - any Altair chart type
    return (
        chart.configure(font=FONT, background="transparent")
        .configure_view(strokeWidth=0)
        .configure_axis(
            gridColor=t.grid,
            gridDash=[2, 3],
            domainColor=t.axis,
            tickColor=t.axis,
            labelColor=t.muted,
            titleColor=t.ink2,
            titleFontWeight="normal",
            labelFontSize=11,
            titleFontSize=11,
            titlePadding=8,
        )
        .configure_axisX(grid=False)
        .configure_header(
            labelColor=t.ink2, titleColor=t.ink2, labelFontSize=12, labelFontWeight="bold"
        )
        .configure_legend(
            labelColor=t.ink2,
            titleColor=t.ink2,
            orient="top",
            direction="horizontal",
            symbolType="circle",
            symbolSize=80,
            labelFontSize=11,
            titleFontSize=11,
        )
        .configure_title(color=t.ink, fontSize=13, fontWeight="bold", anchor="start")
    )


def _label(title: str, unit: str = "") -> str:
    return f"{title} ({unit})" if unit else title


# --------------------------------------------------------------------------- distributions


def histogram(
    values: pl.Series,
    title: str,
    unit: str = "",
    maxbins: int = 30,
    height: int = 200,
    dark: bool = False,
    median: bool = True,
):
    """Single-hue histogram with rounded bar tops, a surface gap and a median marker."""
    t = theme(dark)
    series = values.drop_nulls().cast(pl.Float64())
    df = pl.DataFrame({"x": series}).to_pandas()
    label = _label(title, unit)
    base = alt.Chart(df)
    bars = base.mark_bar(
        color=t.accent, cornerRadiusTopLeft=3, cornerRadiusTopRight=3, binSpacing=2
    ).encode(
        x=alt.X("x:Q", bin=alt.Bin(maxbins=maxbins), title=label, axis=alt.Axis(labelFlush=True)),
        y=alt.Y("count():Q", title="Encounters", axis=alt.Axis(tickMinStep=1)),
        tooltip=[
            alt.Tooltip("x:Q", bin=True, title=label, format=".3~f"),
            alt.Tooltip("count():Q", title="Encounters"),
        ],
    )
    layers = [bars]
    if median and series.len():
        med = float(series.median())
        rule = (
            alt.Chart(pl.DataFrame({"m": [med]}).to_pandas())
            .mark_rule(color=t.ink2, strokeDash=[4, 3])
            .encode(x="m:Q")
        )
        text = (
            alt.Chart(pl.DataFrame({"m": [med], "t": [f"median {med:.3g}"]}).to_pandas())
            .mark_text(align="left", dx=5, dy=-4, color=t.ink2, fontSize=11, baseline="top")
            .encode(x="m:Q", y=alt.value(4), text="t:N")
        )
        layers += [rule, text]
    return _apply(alt.layer(*layers).properties(height=height), t)


def category_counts(values: pl.Series, title: str, dark: bool = False, max_rows: int = 14):
    """Horizontal bars with direct count labels for a categorical column."""
    t = theme(dark)
    counts = (
        values.cast(pl.String())
        .fill_null("(missing)")
        .value_counts(sort=True)
        .rename({values.name: "category"})
        .head(max_rows)
    )
    df = counts.to_pandas()
    base = alt.Chart(df).encode(
        y=alt.Y("category:N", sort="-x", title=None, axis=alt.Axis(labelLimit=180))
    )
    bars = base.mark_bar(color=t.accent, cornerRadiusEnd=3, height={"band": 0.7}).encode(
        x=alt.X("count:Q", title="Encounters", axis=alt.Axis(grid=True)),
        tooltip=[
            alt.Tooltip("category:N", title=title),
            alt.Tooltip("count:Q", title="Encounters"),
        ],
    )
    text = base.mark_text(align="left", dx=4, color=t.ink2, fontSize=11).encode(
        x="count:Q", text="count:Q"
    )
    return _apply(alt.layer(bars, text).properties(height=max(120, 26 * len(df) + 20)), t)


def horizontal_bars(
    frame: pl.DataFrame,
    category: str,
    value: str,
    title: str,
    value_format: str = ",.0f",
    height_per_row: int = 24,
    tooltip_extra: Sequence[str] = (),
    dark: bool = False,
    labels: bool = True,
):
    """Sorted horizontal bars (single hue) with optional direct value labels."""
    t = theme(dark)
    df = frame.sort([value, category], descending=[True, False]).to_pandas()
    base = alt.Chart(df).encode(
        y=alt.Y(
            f"{category}:N",
            sort=None,
            title=None,
            axis=alt.Axis(labelLimit=200, labelOverlap=False),
        )
    )
    bars = base.mark_bar(color=t.accent, cornerRadiusEnd=3, height={"band": 0.65}).encode(
        x=alt.X(f"{value}:Q", title=title, axis=alt.Axis(format=value_format, grid=True)),
        tooltip=[
            alt.Tooltip(f"{category}:N", title=category),
            alt.Tooltip(f"{value}:Q", title=title, format=value_format),
            *[alt.Tooltip(f"{c}:N", title=c) for c in tooltip_extra],
        ],
    )
    layers = [bars]
    if labels:
        layers.append(
            base.mark_text(align="left", dx=4, color=t.ink2, fontSize=11).encode(
                x=f"{value}:Q", text=alt.Text(f"{value}:Q", format=value_format)
            )
        )
    return _apply(alt.layer(*layers).properties(height=max(150, height_per_row * len(df) + 24)), t)


def group_boxplot(
    frame: pl.DataFrame, value: str, by: str, title: str, unit: str = "", dark: bool = False
):
    """Horizontal boxplots of ``value`` per category of ``by`` with jittered points behind.

    Categories use the fixed categorical order; a legend is present and the category axis
    doubles as direct labels, so identity never rests on color alone.
    """
    t = theme(dark)
    df = frame.select(value, by).drop_nulls().with_columns(pl.col(by).cast(pl.String()))
    groups = df.get_column(by).unique().sort().to_list()[:8]
    df = df.filter(pl.col(by).is_in(groups)).to_pandas()
    color = alt.Color(
        f"{by}:N", scale=alt.Scale(domain=groups, range=list(t.series[: len(groups)])), legend=None
    )
    y = alt.Y(f"{by}:N", title=None, sort=groups)
    points = (
        alt.Chart(df)
        .mark_circle(size=18, opacity=0.35)
        .encode(
            x=alt.X(
                f"{value}:Q",
                title=_label(title, unit),
                scale=alt.Scale(zero=False),
                axis=alt.Axis(grid=True),
            ),
            y=y,
            yOffset=alt.YOffset("jitter:Q", scale=alt.Scale(range=[-12, 12])),
            color=color,
            tooltip=[alt.Tooltip(f"{by}:N"), alt.Tooltip(f"{value}:Q", title=title, format=".3~f")],
        )
        .transform_calculate(jitter="random()")
    )
    box = (
        alt.Chart(df)
        .mark_boxplot(
            extent=1.5,
            size=18,
            median={"color": t.surface, "strokeWidth": 2},
            outliers=False,
            ticks=False,
        )
        .encode(x=alt.X(f"{value}:Q", title=_label(title, unit)), y=y, color=color)
    )
    return _apply(alt.layer(points, box).properties(height=max(120, 42 * len(groups) + 30)), t)


def distribution_gallery(
    bins: pl.DataFrame,
    labels: dict[str, str] | None = None,
    units: dict[str, str] | None = None,
    columns: int = 4,
    dark: bool = False,
    width: int = 210,
    height: int = 90,
):
    """Small histogram per variable from :meth:`Cohort.distribution_bins`, independent scales."""
    t = theme(dark)
    labels, units = labels or {}, units or {}
    if bins.is_empty():
        return None
    df = bins.with_columns(
        pl.col(VARIABLE)
        .replace_strict(
            {
                v: _label(labels.get(v, v), units.get(v, ""))
                for v in bins.get_column(VARIABLE).unique()
            },
            default=pl.col(VARIABLE),
        )
        .alias("panel")
    ).to_pandas()
    chart = (
        alt.Chart(df)
        .mark_bar(color=t.accent, cornerRadiusTopLeft=2, cornerRadiusTopRight=2, binSpacing=1)
        .encode(
            x=alt.X("lo:Q", title=None, axis=alt.Axis(labelFontSize=9, tickCount=4)),
            x2="hi:Q",
            y=alt.Y("count:Q", title=None, axis=None),
            y2=alt.datum(0),
            tooltip=[
                alt.Tooltip("panel:N", title="Variable"),
                alt.Tooltip("lo:Q", title="from", format=".3~f"),
                alt.Tooltip("hi:Q", title="to", format=".3~f"),
                alt.Tooltip("count:Q", title="Observations"),
            ],
        )
        .properties(width=width, height=height)
        .facet(
            facet=alt.Facet(
                "panel:N",
                title=None,
                header=alt.Header(
                    labelFontSize=11, labelAlign="left", labelAnchor="start", labelPadding=2
                ),
            ),
            columns=columns,
        )
        .resolve_scale(x="independent", y="independent")
    )
    return _apply(chart, t)


MAX_RAIL_ROWS = 12  # a MIMIC stay has over a hundred kinds of events; the rail shows the busiest


def _rail_rows(
    frame: pl.DataFrame, title: str, duration: tuple[str, str] | None = None
) -> tuple[pl.DataFrame, list[str], str]:
    """Keep the ``MAX_RAIL_ROWS`` kinds that matter most and say how many were left out.

    Intervals (``duration`` names their start and end columns) rank by the hours they cover,
    so one long ventilation episode beats a hundred short saline boluses; events have no
    duration and rank by how often they occur.
    """
    if duration:
        start, end = duration
        weight, how = (pl.col(end) - pl.col(start)).sum(), "by time covered"
    else:
        weight, how = pl.len(), "by frequency"
    kinds = (
        frame.group_by(VARIABLE)
        .agg(weight.alias("w"))
        .sort(["w", VARIABLE], descending=[True, False])
        .get_column(VARIABLE)
        .to_list()
    )
    keep = kinds[:MAX_RAIL_ROWS]
    if len(kinds) > len(keep):
        title = f"{title} ({len(keep)} of {len(kinds)} kinds, {how})"
        frame = frame.filter(pl.col(VARIABLE).is_in(keep))
    return frame, keep, title


def _cycle(palette: Sequence[str], n: int) -> list[str]:
    return [palette[i % len(palette)] for i in range(n)]


# ------------------------------------------------------------------------------- coverage


def coverage_heatmap(
    matrix: pl.DataFrame,
    labels: dict[str, str] | None = None,
    every_hours: float = 4.0,
    dark: bool = False,
    height_per_row: int = 18,
    max_variables: int = 40,
):
    """Variables × hours since admission, color = share of present encounters with data.

    Only the ``max_variables`` best covered variables are drawn; a MIMIC cohort has hundreds.
    """
    t = theme(dark)
    labels = labels or {}
    if matrix.is_empty():
        return None
    order = (
        matrix.group_by(VARIABLE)
        .agg(pl.col("fraction").mean().alias("m"))
        .sort(["m", VARIABLE], descending=[True, False])
        .get_column(VARIABLE)
        .to_list()
    )[:max_variables]
    df = (
        matrix.filter(pl.col(VARIABLE).is_in(order))
        .with_columns(
            pl.col(VARIABLE)
            .replace_strict({v: labels.get(v, v) for v in order}, default=pl.col(VARIABLE))
            .alias("label"),
            (pl.col("hours") + every_hours).alias("hours2"),
        )
        .to_pandas()
    )
    label_order = [labels.get(v, v) for v in order]
    chart = (
        alt.Chart(df)
        .mark_rect(stroke=t.surface, strokeWidth=1)
        .encode(
            x=alt.X(
                "hours:Q", title="Hours since admission", axis=alt.Axis(grid=False, tickMinStep=12)
            ),
            x2="hours2:Q",
            y=alt.Y(
                "label:N",
                sort=label_order,
                title=None,
                axis=alt.Axis(labelLimit=200, labelOverlap=False),
            ),
            color=alt.Color(
                "fraction:Q",
                title="Share of encounters with data",
                scale=alt.Scale(domain=[0, 1], range=list(t.sequential)),
                legend=alt.Legend(format=".0%", gradientLength=160, gradientThickness=8),
            ),
            tooltip=[
                alt.Tooltip("label:N", title="Variable"),
                alt.Tooltip("hours:Q", title="From hour"),
                alt.Tooltip("fraction:Q", title="With data", format=".0%"),
                alt.Tooltip("n:Q", title="Encounters"),
                alt.Tooltip("at_risk:Q", title="Present"),
            ],
        )
        .properties(height=max(140, height_per_row * len(order) + 20))
    )
    return _apply(chart, t)


# ---------------------------------------------------------------------------- time series


def trajectory(
    frame: pl.DataFrame,
    title: str,
    unit: str = "",
    by: str | None = None,
    height: int = 240,
    dark: bool = False,
    with_counts: bool = True,
):
    """Median line with an interquartile band since admission, plus the number of encounters
    contributing per cell underneath (one hue; categorical order with a legend when split)."""
    t = theme(dark)
    df = frame.to_pandas()
    label = _label(title, unit)
    x = alt.X("hours:Q", title="Hours since admission", axis=alt.Axis(labelFlush=True))
    tooltip = [
        alt.Tooltip("hours:Q", title="Hour", format=".0f"),
        alt.Tooltip("median:Q", title="Median", format=".3~f"),
        alt.Tooltip("lo:Q", title="25th pct", format=".3~f"),
        alt.Tooltip("hi:Q", title="75th pct", format=".3~f"),
        alt.Tooltip("n:Q", title="Encounters"),
    ]
    nearest = alt.selection_point(nearest=True, on="pointerover", fields=["hours"], empty=False)
    if by:
        groups = list(dict.fromkeys(df[by].astype(str)))[:8]
        df = df[df[by].astype(str).isin(groups)]
        color = alt.Color(
            f"{by}:N", scale=alt.Scale(domain=groups, range=list(t.series[: len(groups)])), title=by
        )
        base = alt.Chart(df)
        band = base.mark_area(opacity=0.16).encode(x=x, y="lo:Q", y2="hi:Q", color=color)
        line = base.mark_line(strokeWidth=2, interpolate="monotone").encode(
            x=x,
            y=alt.Y("median:Q", title=label, scale=alt.Scale(zero=False, nice=True)),
            color=color,
            tooltip=[alt.Tooltip(f"{by}:N", title=by), *tooltip],
        )
        pts = base.mark_point(filled=True, size=60, stroke=t.surface, strokeWidth=2).encode(
            x=x,
            y="median:Q",
            color=color,
            opacity=alt.condition(nearest, alt.value(1), alt.value(0)),
        )
        counts = base.mark_line(strokeWidth=1.5, interpolate="step-after").encode(
            x=alt.X("hours:Q", title="Hours since admission"),
            y=alt.Y("n:Q", title="Encounters", axis=alt.Axis(tickCount=3)),
            color=color,
            tooltip=[
                alt.Tooltip(f"{by}:N"),
                alt.Tooltip("hours:Q", title="Hour"),
                alt.Tooltip("n:Q", title="Encounters"),
            ],
        )
    else:
        base = alt.Chart(df)
        band = base.mark_area(color=t.accent, opacity=0.16).encode(x=x, y="lo:Q", y2="hi:Q")
        line = base.mark_line(color=t.accent, strokeWidth=2, interpolate="monotone").encode(
            x=x,
            y=alt.Y("median:Q", title=label, scale=alt.Scale(zero=False, nice=True)),
            tooltip=tooltip,
        )
        pts = base.mark_point(
            color=t.accent, filled=True, size=60, stroke=t.surface, strokeWidth=2
        ).encode(x=x, y="median:Q", opacity=alt.condition(nearest, alt.value(1), alt.value(0)))
        counts = base.mark_area(color=t.accent, opacity=0.35, interpolate="step-after").encode(
            x=alt.X("hours:Q", title="Hours since admission"),
            y=alt.Y("n:Q", title="Encounters", axis=alt.Axis(tickCount=3)),
            tooltip=[alt.Tooltip("hours:Q", title="Hour"), alt.Tooltip("n:Q", title="Encounters")],
        )
    selectors = base.mark_point(opacity=0).encode(x=x, y="median:Q").add_params(nearest)
    rule = base.mark_rule(color=t.muted, strokeWidth=1).encode(
        x=x, opacity=alt.condition(nearest, alt.value(0.6), alt.value(0))
    )
    main = alt.layer(band, line, selectors, pts, rule).properties(height=height, width="container")
    if not with_counts:
        return _apply(main, t)
    small = alt.layer(counts).properties(height=48, width="container")
    return _apply(alt.vconcat(main, small, spacing=4).resolve_scale(x="shared", color="shared"), t)


def rank_panels(encounter: Encounter, config: CohortConfig | None = None) -> list[str]:
    """The encounter's variables in the order they deserve a panel.

    Dictionary-matched vital signs and blood gases come first, then ventilation, labs and
    drugs, each group by number of observations; unmatched names follow by their count, and
    variables that never change during the stay (alarm limits, catheter sizes) come last,
    where they do not push heart rate off the default selection. The stored ``plots``
    selection, when set, still wins over this order on the patient page.
    """
    config = config or encounter.config
    unmapped = set(config.unmapped) if config else set()
    dropped = set(config.processing.drop_variables) if config else set()
    stats = (
        encounter.dynamic.group_by(VARIABLE)
        .agg(pl.len().alias("n"), pl.col(VALUE).n_unique().alias("distinct"))
        .iter_rows()
    )
    ranked = []
    for var, n, distinct in stats:
        if distinct <= 1 or var in dropped:
            group = len(PANEL_ROLES) + 2
        else:
            match = None if var in unmapped else match_variable(var)
            role = match[0].role if match else None
            group = (
                PANEL_ROLES.index(role)
                if role in PANEL_ROLES
                else (len(PANEL_ROLES) if match else len(PANEL_ROLES) + 1)
            )
        ranked.append((group, -n, var))
    return [var for _, _, var in sorted(ranked)]


def small_multiples(
    encounter: Encounter,
    variables: Sequence[str],
    every: str | None = None,
    labels: dict[str, str] | None = None,
    units: dict[str, str] | None = None,
    row_height: int = 96,
    dark: bool = False,
    events: bool = True,
    bands: dict[str, pl.DataFrame] | None = None,
):
    """Stacked panels per variable (independent y, shared x and crosshair) with an events rail.

    Different measurements never share one axis; the shared hover crosshair keeps them aligned
    in time, and string events (ventilation modes, notes) sit on a rail above the panels.
    ``bands`` (variable -> :meth:`Cohort.trajectory` frame with ``hours, lo, median, hi``)
    draws the cohort's interquartile band and median behind the patient's line, so a
    clinician sees at once where this patient sits relative to everyone else.
    """
    t = theme(dark)
    labels, units = labels or {}, units or {}
    wide = encounter.wide(every=every, variables=variables)
    present = [v for v in variables if v in wide.columns]
    if not present:
        return None
    hours = (pl.col(TIME).dt.total_seconds() / 3600.0).alias("hours")
    long = (
        wide.select(TIME, *present)
        .unpivot(index=TIME, on=present, variable_name=VARIABLE, value_name=VALUE)
        .filter(pl.col(VALUE).is_not_null())
        .with_columns(hours)
        .drop(TIME)
    )
    df = long.to_pandas()
    nearest = alt.selection_point(
        nearest=True, on="pointerover", fields=["hours"], empty=False, name="xhair"
    )
    x_axis = alt.X("hours:Q", title=None, axis=alt.Axis(labels=False, ticks=False, domain=False))
    x_last = alt.X("hours:Q", title="Hours since admission", axis=alt.Axis(labelFlush=True))
    panels = []
    if events and encounter.intervals.height:
        iv = encounter.intervals.with_columns(
            (pl.col(START).dt.total_seconds() / 3600.0).alias("start_h"),
            (pl.col(END).dt.total_seconds() / 3600.0).alias("end_h"),
        ).select("start_h", "end_h", VARIABLE, VALUE)
        iv, kinds_iv, title_iv = _rail_rows(iv, "Interventions", duration=("start_h", "end_h"))
        rail_iv = (
            alt.Chart(iv.to_pandas())
            .mark_bar(height=10, cornerRadius=2)
            .encode(
                x=alt.X(
                    "start_h:Q", title=None, axis=alt.Axis(labels=False, ticks=False, domain=False)
                ),
                x2="end_h:Q",
                y=alt.Y(f"{VARIABLE}:N", title=None, axis=alt.Axis(labelLimit=160)),
                color=alt.Color(
                    f"{VARIABLE}:N",
                    scale=alt.Scale(domain=kinds_iv, range=_cycle(t.series, len(kinds_iv))),
                    legend=None,
                ),
                tooltip=[
                    alt.Tooltip(f"{VARIABLE}:N", title="Intervention"),
                    alt.Tooltip("start_h:Q", title="From hour", format=".1f"),
                    alt.Tooltip("end_h:Q", title="To hour", format=".1f"),
                    alt.Tooltip(f"{VALUE}:Q", title="Rate / value", format=".3~f"),
                ],
            )
            .properties(
                height=14 * len(kinds_iv) + 10,
                width="container",
                title=alt.Title(title_iv, fontSize=11, color=t.ink2),
            )
        )
        panels.append(rail_iv)
    if events and encounter.events.height:
        ev_frame, kinds, title_ev = _rail_rows(
            encounter.events.with_columns(hours).select("hours", VARIABLE, VALUE), "Events"
        )
        ev = ev_frame.to_pandas()
        rail = (
            alt.Chart(ev)
            .mark_tick(thickness=2, size=14)
            .encode(
                x=x_axis,
                y=alt.Y(f"{VARIABLE}:N", title=None, axis=alt.Axis(labelLimit=160)),
                color=alt.Color(
                    f"{VARIABLE}:N",
                    scale=alt.Scale(domain=kinds, range=_cycle(t.series[1:], len(kinds))),
                    legend=None,
                ),
                tooltip=[
                    alt.Tooltip("hours:Q", title="Hour", format=".1f"),
                    alt.Tooltip(f"{VARIABLE}:N", title="Event"),
                    alt.Tooltip(f"{VALUE}:N", title="Value"),
                ],
            )
            .properties(
                height=14 * len(kinds) + 10,
                width="container",
                title=alt.Title(title_ev, fontSize=11, color=t.ink2),
            )
        )
        panels.append(rail)
    for i, v in enumerate(present):
        sub = df[df[VARIABLE] == v]
        base = alt.Chart(sub)
        last = i == len(present) - 1
        line = base.mark_line(color=t.accent, strokeWidth=1.8, interpolate="monotone").encode(
            x=x_last if last else x_axis,
            y=alt.Y(
                "value:Q",
                title=None,
                scale=alt.Scale(zero=False, nice=True),
                axis=alt.Axis(tickCount=3),
            ),
        )
        layers = []
        band = (bands or {}).get(v)
        if band is not None and band.height:
            bdf = band.select("hours", "lo", "median", "hi").to_pandas()
            layers.append(
                alt.Chart(bdf)
                .mark_area(color=t.muted, opacity=0.14, interpolate="monotone")
                .encode(x="hours:Q", y="lo:Q", y2="hi:Q")
            )
            layers.append(
                alt.Chart(bdf)
                .mark_line(color=t.muted, strokeWidth=1, strokeDash=[4, 3], interpolate="monotone")
                .encode(x="hours:Q", y="median:Q")
            )
        sel = base.mark_point(opacity=0).encode(x="hours:Q", y="value:Q").add_params(nearest)
        pts = base.mark_point(
            color=t.accent, filled=True, size=55, stroke=t.surface, strokeWidth=2
        ).encode(
            x="hours:Q",
            y="value:Q",
            opacity=alt.condition(nearest, alt.value(1), alt.value(0)),
            tooltip=[
                alt.Tooltip("hours:Q", title="Hour", format=".1f"),
                alt.Tooltip("value:Q", title=labels.get(v, v), format=".3~f"),
            ],
        )
        rule = base.mark_rule(color=t.muted, strokeWidth=1).encode(
            x="hours:Q", opacity=alt.condition(nearest, alt.value(0.6), alt.value(0))
        )
        panels.append(
            alt.layer(*layers, line, sel, pts, rule).properties(
                height=row_height,
                width="container",
                title=alt.Title(
                    _label(labels.get(v, v), units.get(v, "")),
                    fontSize=11,
                    color=t.ink2,
                    fontWeight="normal",
                ),
            )
        )
    chart = alt.vconcat(*panels, spacing=6).resolve_scale(x="shared")
    return _apply(chart, t)


def faceted_histogram(
    frame: pl.DataFrame, value: str, by: str, title: str, maxbins: int = 20, dark: bool = False
):
    """Kept for compatibility: prefer :func:`group_boxplot` for comparisons."""
    return group_boxplot(frame, value, by, title, dark=dark)


def site_distributions(
    bins: pl.DataFrame,
    labels: dict[str, str] | None = None,
    units: dict[str, str] | None = None,
    columns: int = 3,
    dark: bool = False,
    width: int = 260,
    height: int = 110,
):
    """One panel per variable, one normalized step-histogram per site, shared bins.

    Sites of different size compare because each curve is a share of that site's
    observations, not a count. From :func:`dea.quality.site_bins`.
    """
    t = theme(dark)
    labels, units = labels or {}, units or {}
    if bins.is_empty():
        return None
    names = bins.get_column(VARIABLE).unique().to_list()
    df = bins.with_columns(
        pl.col(VARIABLE)
        .replace_strict(
            {v: _label(labels.get(v, v), units.get(v, "")) for v in names},
            default=pl.col(VARIABLE),
        )
        .alias("panel")
    ).to_pandas()
    sites = sorted(bins.get_column("site").unique().to_list())
    color = alt.Color(
        "site:N",
        title="Site",
        scale=alt.Scale(domain=sites, range=list(t.series)[: len(sites)]),
        legend=alt.Legend(orient="top", direction="horizontal", columns=4, labelLimit=170),
    )
    base = alt.Chart(df).encode(
        x=alt.X("lo:Q", title=None, axis=alt.Axis(labelFontSize=9, tickCount=4)),
        y=alt.Y("fraction:Q", title=None, axis=None, stack=None),  # overlay, never stack
        color=color,
    )
    area = base.mark_area(interpolate="step-after", opacity=0.22 if len(sites) <= 4 else 0.1)
    line = base.mark_line(interpolate="step-after", strokeWidth=1.6).encode(
        tooltip=[
            alt.Tooltip("panel:N", title="Variable"),
            alt.Tooltip("site:N", title="Site"),
            alt.Tooltip("lo:Q", title="from", format=".3~f"),
            alt.Tooltip("hi:Q", title="to", format=".3~f"),
            alt.Tooltip("fraction:Q", title="Share", format=".1%"),
            alt.Tooltip("count:Q", title="Observations"),
        ]
    )
    chart = (
        alt.layer(area, line)
        .properties(width=width, height=height)
        .facet(
            facet=alt.Facet(
                "panel:N",
                title=None,
                header=alt.Header(
                    labelFontSize=11, labelAlign="left", labelAnchor="start", labelPadding=2
                ),
            ),
            columns=columns,
        )
        .resolve_scale(x="independent", y="independent")
    )
    return _apply(chart, t)


def site_matrix(
    profile: pl.DataFrame,
    value: str,
    title: str,
    fmt: str = ".0%",
    labels: dict[str, str] | None = None,
    dark: bool = False,
    domain: tuple[float, float] | None = (0, 1),
    log: bool = False,
):
    """Variables × sites heatmap of one column of :func:`dea.quality.site_profile`."""
    t = theme(dark)
    labels = labels or {}
    profile = profile.filter(pl.col(value).is_not_null())
    if profile.is_empty():
        return None
    order = (
        profile.group_by(VARIABLE)
        .agg(pl.col(value).mean().alias("m"))
        .sort(["m", VARIABLE], descending=[True, False])
        .get_column(VARIABLE)
        .to_list()
    )
    n_sites = profile.get_column("site").n_unique()
    df = profile.with_columns(
        pl.col(VARIABLE)
        .replace_strict({v: labels.get(v, v) for v in order}, default=pl.col(VARIABLE))
        .alias("label")
    ).to_pandas()
    scale = alt.Scale(range=list(t.sequential), type="log" if log else "linear")
    if domain and not log:
        scale = alt.Scale(domain=list(domain), range=list(t.sequential))
    base = alt.Chart(df).encode(
        x=alt.X(
            "site:N",
            title=None,
            axis=alt.Axis(
                labelAngle=0 if n_sites <= 6 else -35,
                orient="top",
                labelLimit=110 if n_sites <= 6 else 150,
                labelFontSize=10,
            ),
        ),
        y=alt.Y(
            "label:N",
            sort=[labels.get(v, v) for v in order],
            title=None,
            axis=alt.Axis(labelLimit=220, labelOverlap=False, labelFontSize=10),
        ),
    )
    rect = base.mark_rect(stroke=t.surface, strokeWidth=1).encode(
        color=alt.Color(f"{value}:Q", title=title, scale=scale, legend=None),
        tooltip=[
            alt.Tooltip("label:N", title="Variable"),
            alt.Tooltip("site:N", title="Site"),
            alt.Tooltip(f"{value}:Q", title=title, format=fmt),
            alt.Tooltip("n_encounters:Q", title="Encounters"),
            alt.Tooltip("n_obs:Q", title="Observations"),
        ],
    )
    text = base.mark_text(fontSize=10).encode(
        text=alt.Text(f"{value}:Q", format=fmt),
        color=alt.condition(
            f"datum.{value} > {0.6 if not log else 1e9}", alt.value(t.surface), alt.value(t.ink)
        ),
    )
    chart = alt.layer(rect, text).properties(
        width=alt.Step(64 if n_sites <= 6 else 46),
        height=alt.Step(20),
        title=alt.Title(title, anchor="start"),
    )
    return _apply(chart, t)

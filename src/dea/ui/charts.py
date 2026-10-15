"""Altair chart builders with one consistent, colorblind-safe look.

Rules applied throughout: one hue for single-series magnitude, a fixed categorical
order for identity, no dual axes (different scales become small multiples), thin marks
with rounded data-ends, recessive grid, tooltips everywhere.
"""

from __future__ import annotations

from collections.abc import Sequence

import altair as alt
import polars as pl

from dea.core import TIME, VALUE, VARIABLE, Encounter

# Reference palette (validated for CVD separation in adjacent order).
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
ACCENT = SERIES[0]
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
INK_SECONDARY = "#52514e"
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def _theme(chart: alt.Chart | alt.LayerChart | alt.FacetChart):
    return (
        chart.configure(font=FONT)
        .configure_view(strokeWidth=0)
        .configure_axis(
            gridColor=GRID,
            domainColor=AXIS,
            tickColor=AXIS,
            labelColor=MUTED,
            titleColor=INK_SECONDARY,
            titleFontWeight="normal",
            labelFontSize=11,
            titleFontSize=11,
        )
        .configure_header(labelColor=INK_SECONDARY, titleColor=INK_SECONDARY, labelFontSize=12)
        .configure_legend(labelColor=INK_SECONDARY, titleColor=INK_SECONDARY)
    )


def histogram(values: pl.Series, title: str, unit: str = "", maxbins: int = 30, height: int = 220):
    """Single-hue histogram with rounded bar tops and a surface gap between bars."""
    df = pl.DataFrame({"x": values.drop_nulls().cast(pl.Float64())}).to_pandas()
    label = f"{title} ({unit})" if unit else title
    chart = (
        alt.Chart(df)
        .mark_bar(color=ACCENT, cornerRadiusTopLeft=4, cornerRadiusTopRight=4, binSpacing=2)
        .encode(
            x=alt.X("x:Q", bin=alt.Bin(maxbins=maxbins), title=label),
            y=alt.Y("count():Q", title="Encounters" if "stay" in title.lower() else "Count"),
            tooltip=[
                alt.Tooltip("x:Q", bin=True, title=label),
                alt.Tooltip("count():Q", title="Count"),
            ],
        )
        .properties(height=height)
    )
    return _theme(chart)


def horizontal_bars(
    frame: pl.DataFrame,
    category: str,
    value: str,
    title: str,
    value_format: str = ",.0f",
    height_per_row: int = 26,
    tooltip_extra: Sequence[str] = (),
):
    """Sorted horizontal bars for one measure across categories (single hue)."""
    df = frame.sort([value, category], descending=[True, False]).to_pandas()
    chart = (
        alt.Chart(df)
        .mark_bar(color=ACCENT, cornerRadiusEnd=4)
        .encode(
            y=alt.Y(
                f"{category}:N",
                sort=None,
                title=None,
                axis=alt.Axis(labelLimit=180, labelOverlap=False),
            ),
            x=alt.X(f"{value}:Q", title=title, axis=alt.Axis(format=value_format)),
            tooltip=[
                alt.Tooltip(f"{category}:N", title=category),
                alt.Tooltip(f"{value}:Q", title=title, format=value_format),
                *[alt.Tooltip(f"{c}:N", title=c) for c in tooltip_extra],
            ],
        )
        .properties(height=max(160, height_per_row * len(df) + 30))
    )
    return _theme(chart)


def category_counts(values: pl.Series, title: str, height: int = 200):
    counts = (
        values.cast(pl.String())
        .fill_null("(missing)")
        .value_counts(sort=True)
        .rename({values.name: "category"})
    )
    return horizontal_bars(counts, "category", "count", f"{title}: encounters")


def small_multiples(
    encounter: Encounter,
    variables: Sequence[str],
    every: str | None = None,
    labels: dict[str, str] | None = None,
    units: dict[str, str] | None = None,
    row_height: int = 110,
    width: int = 900,
):
    """One small line chart per variable, independent y-scales, shared time axis + crosshair.

    Different measurements never share one axis; stacking small panels keeps every scale
    honest while the shared x axis preserves alignment in time.
    """
    labels = labels or {}
    units = units or {}
    wide = encounter.wide(every=every, variables=variables)
    present = [v for v in variables if v in wide.columns]
    if not present:
        return None
    long = (
        wide.select(TIME, *present)
        .unpivot(index=TIME, on=present, variable_name=VARIABLE, value_name=VALUE)
        .filter(pl.col(VALUE).is_not_null())
        .with_columns((pl.col(TIME).dt.total_seconds() / 3600.0).alias("hours"))
        .with_columns(
            pl.col(VARIABLE)
            .replace_strict(
                {
                    v: (labels.get(v, v) + (f" ({units[v]})" if units.get(v) else ""))
                    for v in present
                },
                default=pl.col(VARIABLE),
            )
            .alias("panel")
        )
        .drop(TIME)
    )
    order = [labels.get(v, v) + (f" ({units[v]})" if units.get(v) else "") for v in present]
    df = long.to_pandas()
    nearest = alt.selection_point(nearest=True, on="pointerover", fields=["hours"], empty=False)
    base = alt.Chart(df).encode(x=alt.X("hours:Q", title="Hours since admission"))
    line = base.mark_line(color=ACCENT, strokeWidth=2, interpolate="monotone").encode(
        y=alt.Y("value:Q", title=None, scale=alt.Scale(zero=False, nice=True))
    )
    selectors = base.mark_point(opacity=0).encode(y=alt.Y("value:Q")).add_params(nearest)
    points = base.mark_point(
        color=ACCENT, filled=True, size=70, stroke="#fcfcfb", strokeWidth=2
    ).encode(
        y=alt.Y("value:Q"),
        opacity=alt.condition(nearest, alt.value(1), alt.value(0)),
        tooltip=[
            alt.Tooltip("panel:N", title="Variable"),
            alt.Tooltip("hours:Q", title="Hours", format=".1f"),
            alt.Tooltip("value:Q", title="Value", format=".3~f"),
        ],
    )
    rule = base.mark_rule(color=MUTED, strokeWidth=1).encode(
        opacity=alt.condition(nearest, alt.value(0.6), alt.value(0))
    )
    layered = alt.layer(line, selectors, points, rule).properties(height=row_height, width=width)
    chart = layered.facet(
        row=alt.Row(
            "panel:N",
            sort=order,
            title=None,
            header=alt.Header(
                labelAngle=0, labelAlign="left", labelOrient="top", labelAnchor="start"
            ),
        )
    ).resolve_scale(y="independent")
    return _theme(chart)


def faceted_histogram(frame: pl.DataFrame, value: str, by: str, title: str, maxbins: int = 20):
    """Small-multiple histograms of ``value`` per category of ``by`` (shared x, one hue)."""
    df = frame.select(value, by).drop_nulls().with_columns(pl.col(by).cast(pl.String())).to_pandas()
    chart = (
        alt.Chart(df)
        .mark_bar(color=ACCENT, cornerRadiusTopLeft=4, cornerRadiusTopRight=4, binSpacing=2)
        .encode(
            x=alt.X(f"{value}:Q", bin=alt.Bin(maxbins=maxbins), title=title),
            y=alt.Y("count():Q", title="Encounters"),
            tooltip=[alt.Tooltip(f"{by}:N"), alt.Tooltip("count():Q", title="Encounters")],
        )
        .properties(height=120, width=220)
        .facet(column=alt.Column(f"{by}:N", title=None))
    )
    return _theme(chart)


def trajectory(
    frame: pl.DataFrame, title: str, unit: str = "", by: str | None = None, height: int = 260
):
    """Median line with an interquartile band per hour since admission, optionally per group.

    One hue for a single series; the fixed categorical order with a legend for up to eight
    groups (text stays in ink colors, never in the series color).
    """
    df = frame.to_pandas()
    label = f"{title} ({unit})" if unit else title
    x = alt.X("hours:Q", title="Hours since admission")
    tooltip = [
        alt.Tooltip("hours:Q", title="Hour", format=".0f"),
        alt.Tooltip("median:Q", title="Median", format=".3~f"),
        alt.Tooltip("lo:Q", title="25th pct", format=".3~f"),
        alt.Tooltip("hi:Q", title="75th pct", format=".3~f"),
        alt.Tooltip("n:Q", title="Encounters"),
    ]
    base = alt.Chart(df)
    if by:
        groups = list(dict.fromkeys(df[by].astype(str)))[:8]
        df = df[df[by].astype(str).isin(groups)]
        base = alt.Chart(df)
        color = alt.Color(
            f"{by}:N", scale=alt.Scale(domain=groups, range=SERIES[: len(groups)]), title=by
        )
        band = base.mark_area(opacity=0.18).encode(x=x, y="lo:Q", y2="hi:Q", color=color)
        line = base.mark_line(strokeWidth=2, interpolate="monotone").encode(
            x=x,
            y=alt.Y("median:Q", title=label, scale=alt.Scale(zero=False)),
            color=color,
            tooltip=[alt.Tooltip(f"{by}:N", title=by), *tooltip],
        )
    else:
        band = base.mark_area(color=ACCENT, opacity=0.18).encode(x=x, y="lo:Q", y2="hi:Q")
        line = base.mark_line(color=ACCENT, strokeWidth=2, interpolate="monotone").encode(
            x=x, y=alt.Y("median:Q", title=label, scale=alt.Scale(zero=False)), tooltip=tooltip
        )
    return _theme(alt.layer(band, line).properties(height=height))

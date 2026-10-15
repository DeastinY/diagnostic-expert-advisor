"""One-page HTML report of a cohort: summary, charts and profile tables.

Produces a single file a clinician can open or forward without running anything. With
``dea[report]`` (vl-convert) charts are embedded as static SVG and the file works offline and
prints; without it, charts load vega-embed from a CDN. Requires ``dea[ui]`` for Altair.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from dea.core import Cohort
from dea.core.time import format_duration
from dea.profile import CohortProfile, profile_cohort

_CSS = """
body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:#0b0b0b;background:#fcfcfb;
margin:0;padding:32px;max-width:1100px}h1{margin:0 0 4px}h2{margin:32px 0 8px;font-size:1.2rem}
.muted{color:#52514e}.kpis{display:flex;gap:32px;flex-wrap:wrap;margin:16px 0}
.kpi .v{font-size:1.8rem}.kpi .l{color:#52514e;font-size:.85rem}
table{border-collapse:collapse;font-size:.85rem;width:100%}th,td{text-align:left;padding:4px 8px;
border-bottom:1px solid #e1e0d9}th{color:#52514e;font-weight:normal}
.warn{background:#fff8e1;border-left:3px solid #eda100;padding:8px 12px;margin:6px 0}
.chart{width:100%}.chart svg{max-width:100%;height:auto}
"""

_VEGA = (
    '<script src="https://cdn.jsdelivr.net/npm/vega@5.30.0"></script>'
    '<script src="https://cdn.jsdelivr.net/npm/vega-lite@5.21.0"></script>'
    '<script src="https://cdn.jsdelivr.net/npm/vega-embed@6.26.0"></script>'
)


def _table(frame: pl.DataFrame, columns: list[str], limit: int = 60) -> str:
    cols = [c for c in columns if c in frame.columns]
    head = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
    rows = []
    for row in frame.select(cols).head(limit).iter_rows():
        cells = "".join(
            f"<td>{html.escape(f'{v:.3g}' if isinstance(v, float) else str(v))}</td>" for v in row
        )
        rows.append(f"<tr>{cells}</tr>")
    more = (
        f'<p class="muted">… {frame.height - limit} more rows</p>' if frame.height > limit else ""
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>{more}"


def render_html(
    cohort: Cohort, profile: CohortProfile | None = None, max_trajectories: int = 4
) -> str:
    """Build the report as an HTML string."""
    from dea.ui import charts  # altair lives in the ui extra

    profile = profile or profile_cohort(cohort)
    cfg = cohort.config
    feats = cohort.features()
    summary = cohort.summary()
    specs: list[tuple[str, str, str]] = []  # (element id, heading, vega-lite json)
    try:
        import vl_convert as vlc
    except ImportError:  # pragma: no cover - depends on optional extra
        vlc = None

    def add(name: str, heading: str, chart) -> None:  # noqa: ANN001
        if chart is None:
            return
        width = 1000 if vlc else "container"
        specs.append((name, heading, chart.properties(width=width).to_json()))

    add(
        "los", "Length of stay", charts.histogram(feats["los_hours"] / 24, "Length of stay", "days")
    )
    counts = cohort.variable_counts().rename({"variable": "name"}).head(40)
    if counts.height:
        add(
            "obs",
            "Observations per variable",
            charts.horizontal_bars(counts, "name", "n_observations", "Observations"),
        )
    for var in [v for v in cfg.plots if v in set(cohort.variables)][:max_trajectories]:
        traj = cohort.trajectory(var, every="4h", max_hours=24 * 7)
        if traj.height:
            add(
                f"traj_{var}",
                f"{cfg.label(var)}: cohort trajectory (first week)",
                charts.trajectory(traj, cfg.label(var), cfg.variable_units.get(var, "")),
            )

    kpis = [
        ("Encounters", f"{summary.n_encounters:,}"),
        ("Variables", str(summary.n_variables)),
        ("Observations", f"{summary.n_observations:,}"),
        ("Median stay", format_duration(summary.los_hours_median * 3600)),
        (
            "Typical interval",
            profile.sampling.median_interval_s
            and format_duration(profile.sampling.median_interval_s)
            or "n/a",
        ),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><div class="v">{html.escape(v)}</div><div class="l">{k}</div></div>'
        for k, v in kpis
    )
    warn_html = "".join(f'<div class="warn">{html.escape(w)}</div>' for w in profile.warnings)
    if vlc:
        chart_html = "".join(
            f'<section><h2>{html.escape(h)}</h2><div id="{i}" class="chart">'
            f"{vlc.vegalite_to_svg(spec)}</div></section>"
            for i, h, spec in specs
        )
        embed = ""
    else:
        chart_html = "".join(
            f'<section><h2>{html.escape(h)}</h2><div id="{i}" class="chart"></div></section>'
            for i, h, _ in specs
        )
        embed = "".join(f"vegaEmbed('#{i}', {spec}, {{actions:false}});" for i, _, spec in specs)
    tags_html = ""
    if cfg.tags:
        tags = cohort.tag_matrix()
        tag_rows = pl.DataFrame(
            {
                "tag": [r.label for r in cfg.tags],
                "encounters": [int(tags[r.label].sum()) for r in cfg.tags],
                "share": [f"{100 * tags[r.label].mean():.0f}%" for r in cfg.tags],
                "definition": [r.where for r in cfg.tags],
            }
        )
        tags_html = "<h2>Tags</h2>" + _table(tag_rows, ["tag", "encounters", "share", "definition"])
    variables_html = (
        _table(
            profile.variables_frame(),
            ["name", "coverage", "n_observations", "interval", "min", "median", "max", "flags"],
        )
        if profile.variables
        else ""
    )
    events_html = (
        (
            "<h2>Events</h2>"
            + _table(
                profile.events_frame(),
                ["name", "coverage", "n_observations", "n_unique", "top_values"],
            )
        )
        if profile.events
        else ""
    )
    static_html = (
        (
            "<h2>Static columns</h2>"
            + _table(
                profile.static_frame(),
                ["name", "kind", "n_missing", "n_unique", "min", "max", "mean", "top_values"],
            )
        )
        if profile.static_columns
        else ""
    )
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    title = html.escape(cfg.name)
    head = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{title} · DEA report</title><style>{_CSS}</style>{'' if vlc else _VEGA}</head>"
    )
    intro = (
        f'<h1>{title}</h1><p class="muted">{html.escape(cfg.description)}<br>'
        f"Generated by the Diagnostic Expert Advisor on {stamp}.</p>"
    )
    body = (
        f'<div class="kpis">{kpi_html}</div>{warn_html}{chart_html}{tags_html}'
        f"<h2>Variables</h2>{variables_html}{events_html}{static_html}"
    )
    return f"{head}<body>{intro}{body}<script>{embed}</script></body></html>"


def write_report(cohort: Cohort, path: Path | str, profile: CohortProfile | None = None) -> Path:
    out = Path(path)
    out.write_text(render_html(cohort, profile), encoding="utf-8")
    return out

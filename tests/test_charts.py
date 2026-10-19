from __future__ import annotations

import polars as pl
import pytest

pytest.importorskip("altair")
from dea import charts  # noqa: E402
from dea.core import Cohort  # noqa: E402


def _ok(chart) -> dict:  # noqa: ANN001
    spec = chart.to_dict()
    assert spec["$schema"].startswith("https://vega.github.io/schema/vega-lite")
    return spec


def test_distribution_charts(cohort: Cohort) -> None:
    feats = cohort.features()
    for dark in (False, True):
        _ok(charts.histogram(feats["los_hours"], "Stay", "h", dark=dark))
        _ok(charts.category_counts(cohort.static["sex"], "Sex", dark=dark))
        _ok(
            charts.horizontal_bars(
                pl.DataFrame({"a": ["x", "y"], "v": [2.0, 1.0]}), "a", "v", "V", dark=dark
            )
        )
        _ok(charts.group_boxplot(feats, "HR__mean", "sex", "Heart rate", "1/min", dark=dark))
    gallery = charts.distribution_gallery(
        cohort.distribution_bins(), {"HR": "Heart rate"}, {"HR": "1/min"}
    )
    spec = _ok(gallery)
    assert "facet" in spec and spec["columns"] == 4
    assert (
        charts.distribution_gallery(
            pl.DataFrame(
                schema={
                    "variable": pl.String(),
                    "lo": pl.Float64(),
                    "hi": pl.Float64(),
                    "count": pl.UInt32(),
                }
            )
        )
        is None
    )


def test_coverage_matrix_and_heatmap(cohort: Cohort) -> None:
    m = cohort.coverage_matrix(every="4h", max_hours=48)
    assert m.columns == ["variable", "hours", "n", "at_risk", "fraction"]
    assert (m["fraction"] <= 1.0).all() and (m["hours"] % 4 == 0).all()
    hr0 = m.filter((pl.col("variable") == "HR") & (pl.col("hours") == 0)).row(0, named=True)
    assert hr0["n"] == len(cohort) and hr0["fraction"] == 1.0
    spec = _ok(charts.coverage_heatmap(m, {"HR": "Heart rate"}, 4.0))
    assert spec["mark"]["type"] == "rect"


def test_distribution_bins(cohort: Cohort) -> None:
    b = cohort.distribution_bins(variables=["HR", "SOFA"], bins=10)
    assert set(b["variable"].unique()) == {"HR", "SOFA"}
    assert (b.filter(pl.col("variable") == "HR")["count"].sum()) > 0
    assert (b["hi"] > b["lo"]).all()


def test_trajectory_and_small_multiples(cohort: Cohort) -> None:
    traj = cohort.trajectory("HR", every="4h", max_hours=48)
    spec = _ok(charts.trajectory(traj, "Heart rate", "1/min"))
    assert "vconcat" in spec and len(spec["vconcat"]) == 2
    _ok(charts.trajectory(traj, "Heart rate", with_counts=False))
    split = cohort.trajectory("HR", every="4h", by="sex", max_hours=48)
    _ok(charts.trajectory(split, "Heart rate", by="sex", dark=True))
    enc = cohort.get(cohort.encounter_ids[0])
    spec = _ok(charts.small_multiples(enc, ["HR", "MAP"], "1h"))
    rails = 1 + (1 if enc.intervals.height else 0)  # events rail, interventions rail
    assert len(spec["vconcat"]) == 2 + rails
    spec = _ok(charts.small_multiples(enc, ["HR"], None, events=False))
    assert len(spec["vconcat"]) == 1
    assert charts.small_multiples(enc, ["nope"]) is None


def test_coverage_heatmap_caps_variables_and_serializes():
    """A MIMIC-sized variable list must not trip Altair's row limit."""
    import polars as pl

    from dea import charts

    hours = [h * 4.0 for h in range(42)]
    rows = {
        "variable": [f"v{i}" for i in range(400) for _ in hours],
        "hours": hours * 400,
        "n": [1] * (400 * len(hours)),
        "at_risk": [2] * (400 * len(hours)),
        "fraction": [0.5] * (400 * len(hours)),
    }
    chart = charts.coverage_heatmap(pl.DataFrame(rows), max_variables=40)
    spec = chart.to_dict()  # raised MaxRowsError before
    assert len(spec["datasets"][next(iter(spec["datasets"]))]) == 40 * len(hours)


def test_coverage_matrix_starts_at_admission(cohort: Cohort) -> None:
    # a chart row dated a year before admission must not stretch the heatmap's axis
    early = cohort.dynamic.head(1).with_columns(pl.duration(hours=-8000).alias("time"))
    shifted = Cohort(
        cohort.static,
        pl.concat([cohort.dynamic, early]),
        events=cohort.events,
        config=cohort.config,
    )
    m = shifted.coverage_matrix(every="4h", max_hours=48)
    assert (m["hours"] >= 0).all()


def test_rank_panels_puts_vital_signs_first_and_constants_last(cohort: Cohort) -> None:
    from dea.charts import rank_panels

    e = cohort.get(cohort.static["encounter_id"][0])
    assert e.variables[0] == "Bed_Number"  # alphabetical, what the page used to show first
    ranked = rank_panels(e, cohort.config)
    assert set(ranked) == set(e.variables)
    assert set(ranked[:5]) == {"HR", "MAP", "RR", "SpO2", "Temp"}  # the vital signs, by count
    assert ranked.index("Horowitz") < ranked.index("Lactate") < ranked.index("Norepinephrine")
    assert ranked[-1] == "Bed_Number"  # constant within the stay


def test_interventions_rail_ranks_kinds_by_hours_covered(cohort: Cohort) -> None:
    # one long ventilation episode among many kinds of short boluses: by frequency it would
    # never make the rail, by time covered it leads it; events keep ranking by frequency
    from datetime import timedelta

    from dea.core import Encounter
    from dea.core.model import END, START

    enc = cohort.get(cohort.encounter_ids[0])
    rows = [("Invasive Ventilation", 0.0, 100.0, 1.0)]
    for k in range(20):  # 20 kinds x 5 boluses of a quarter hour each
        rows += [(f"Bolus {k:02d}", float(h), h + 0.25, 250.0) for h in range(5)]
    intervals = pl.DataFrame(
        {
            "encounter_id": [enc.id] * len(rows),
            "variable": [r[0] for r in rows],
            START: [timedelta(hours=r[1]) for r in rows],
            END: [timedelta(hours=r[2]) for r in rows],
            "value": [r[3] for r in rows],
        }
    )
    frame = intervals.select(
        (pl.col(START).dt.total_seconds() / 3600).alias("start_h"),
        (pl.col(END).dt.total_seconds() / 3600).alias("end_h"),
        "variable",
        "value",
    )
    kept, kinds, title = charts._rail_rows(frame, "Interventions", duration=("start_h", "end_h"))
    assert kinds[0] == "Invasive Ventilation" and len(kinds) == charts.MAX_RAIL_ROWS
    assert title == "Interventions (12 of 21 kinds, by time covered)"
    assert kept.height == 1 + 11 * 5
    _, by_count, title = charts._rail_rows(frame, "Events")
    assert "Invasive Ventilation" not in by_count and title.endswith("by frequency)")

    eid = pl.lit(enc.id).alias("encounter_id")  # an Encounter strips the id from its frames
    with_vent = Encounter(
        enc.id,
        enc.static,
        enc.dynamic.with_columns(eid),
        enc.events.with_columns(eid),
        intervals,
        enc.config,
    )
    spec = _ok(charts.small_multiples(with_vent, ["HR"], None))
    rail = spec["vconcat"][0]
    assert "by time covered" in rail["title"]["text"]
    assert rail["encoding"]["color"]["scale"]["domain"][0] == "Invasive Ventilation"


def test_stacked_counts_and_colorful_bars(cohort: Cohort) -> None:
    static = cohort.static
    cols = [c for c in static.columns if c != "encounter_id"]
    spec = _ok(charts.stacked_counts(static, cols[0], cols[1], "A", by_title="B", normalize=True))
    assert spec["encoding"]["x"]["stack"] is True and spec["encoding"]["x"]["field"] == "share"
    assert spec["encoding"]["color"]["scale"]["range"]
    plain = _ok(charts.category_counts(static[cols[0]], "A"))
    colored = _ok(charts.category_counts(static[cols[0]], "A", colorful=True))
    assert (
        "color" not in plain["layer"][0]["encoding"] and "color" in colored["layer"][0]["encoding"]
    )


def test_small_multiples_tolerates_a_variable_listed_twice(cohort: Cohort) -> None:
    enc = cohort.get(cohort.encounter_ids[0])
    spec = _ok(charts.small_multiples(enc, ["HR", "MAP", "HR"], "1h"))
    titles = [p.get("title", {}).get("text") for p in spec["vconcat"] if "title" in p]
    assert sum(1 for t in titles if t and t.startswith("HR")) == 1

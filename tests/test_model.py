from __future__ import annotations

from datetime import timedelta

import polars as pl
import pytest

from dea.core import (
    ENCOUNTER_ID,
    TIME,
    VALUE,
    VARIABLE,
    Cohort,
    CohortConfig,
    Encounter,
    FilterError,
    ProcessingConfig,
    Rule,
)
from dea.core.model import long_to_wide


def _long(rows: list[tuple[str, int, str, float]]) -> pl.DataFrame:
    df = pl.DataFrame(rows, schema=[ENCOUNTER_ID, "secs", VARIABLE, VALUE], orient="row")
    return df.with_columns(pl.duration(seconds=pl.col("secs")).alias(TIME)).drop("secs")


@pytest.fixture
def tiny() -> Cohort:
    static = pl.DataFrame({ENCOUNTER_ID: ["b", "a"], "age": [70, 40], "sex": ["F", "M"]})
    dynamic = _long(
        [
            ("a", 0, "HR", 80.0),
            ("a", 60, "HR", 82.0),
            ("a", 120, "HR", 84.0),
            ("a", 0, "Lactate", 1.0),
            ("a", 7200, "Lactate", 3.0),
            ("b", 0, "HR", 100.0),
            ("b", 3600, "HR", 110.0),
        ]
    )
    config = CohortConfig(
        name="tiny",
        filters=[Rule(label="Tachy", where='"HR__max" > 90')],
        tags=[Rule(label="Old", where="age >= 65")],
    )
    return Cohort(static, dynamic, config=config)


def test_cohort_basics(tiny: Cohort) -> None:
    assert len(tiny) == 2
    assert tiny.encounter_ids == ["b", "a"]
    assert "a" in tiny and "zzz" not in tiny
    assert tiny.variables == ["HR", "Lactate"]
    assert tiny.static_columns == ["age", "sex"]
    assert repr(tiny) == "Cohort(name='tiny', encounters=2)"


def test_static_requires_unique_ids() -> None:
    static = pl.DataFrame({ENCOUNTER_ID: ["a", "a"]})
    with pytest.raises(ValueError, match="duplicate"):
        Cohort(static, _long([("a", 0, "HR", 1.0)]))


def test_dynamic_schema_is_enforced() -> None:
    bad = pl.DataFrame({ENCOUNTER_ID: ["a"], TIME: [0], VARIABLE: ["HR"]})
    with pytest.raises(ValueError, match="missing columns"):
        Cohort(pl.DataFrame({ENCOUNTER_ID: ["a"]}), bad)


def test_get_encounter_keys_on_id_not_position(tiny: Cohort) -> None:
    e = tiny.get("a")
    assert isinstance(e, Encounter)
    assert e.static == {"age": 40, "sex": "M"}
    assert e.variables == ["HR", "Lactate"]
    assert e.length_of_stay == timedelta(hours=2)
    assert tiny["b"].static["age"] == 70
    with pytest.raises(KeyError):
        tiny.get("nope")


def test_encounters_bulk_matches_single(tiny: Cohort) -> None:
    bulk = {e.id: e for e in tiny.encounters()}
    assert set(bulk) == {"a", "b"}
    assert bulk["a"].dynamic.height == tiny.get("a").dynamic.height


def test_encounter_without_observations_is_allowed() -> None:
    static = pl.DataFrame({ENCOUNTER_ID: ["a", "empty"]})
    cohort = Cohort(static, _long([("a", 0, "HR", 1.0)]))
    e = cohort.get("empty")
    assert e.n_observations == 0
    assert e.length_of_stay == timedelta(0)
    assert cohort.features().filter(pl.col(ENCOUNTER_ID) == "empty")["n_observations"][0] == 0


def test_wide_raw(tiny: Cohort) -> None:
    w = tiny.get("a").wide()
    assert w.columns == [TIME, "HR", "Lactate"]
    assert w.height == 4  # 0, 60, 120, 7200 seconds
    assert w["HR"].to_list() == [80.0, 82.0, 84.0, None]


def test_wide_resample_and_fill(tiny: Cohort) -> None:
    w = tiny.get("a").wide(every="1h", fill="forward")
    assert w[TIME].dt.total_seconds().to_list() == [0, 3600, 7200]
    assert w["HR"].to_list() == [82.0, 82.0, 82.0]  # mean of 80,82,84 then forward-filled
    assert w["Lactate"].to_list() == [1.0, 1.0, 3.0]
    w2 = tiny.get("a").wide(every="1h", fill="interpolate")
    assert w2["Lactate"].to_list() == [1.0, 2.0, 3.0]


def test_processing_overrides_and_drops() -> None:
    proc = ProcessingConfig(
        resample_every="1h", fill="none", fill_overrides={"Lactate": "zero"}, drop_variables=["HR"]
    )
    long = _long([("a", 0, "HR", 1.0), ("a", 0, "Lactate", 1.0), ("a", 7200, "Lactate", 3.0)])
    w = long_to_wide(long, proc)
    assert w.columns == [ENCOUNTER_ID, TIME, "Lactate"]
    assert w["Lactate"].to_list() == [1.0, 0.0, 3.0]


def test_features_and_filters(tiny: Cohort) -> None:
    feats = tiny.features()
    assert feats[ENCOUNTER_ID].to_list() == ["a", "b"]
    row = feats.filter(pl.col(ENCOUNTER_ID) == "a").row(0, named=True)
    assert row["HR__max"] == 84.0 and row["HR__first"] == 80.0 and row["HR__last"] == 84.0
    assert row["Lactate__count"] == 2.0
    assert row["los_hours"] == 2.0 and row["n_variables"] == 2
    assert tiny.filter('"HR__max" > 90').encounter_ids == ["b"]
    assert tiny.filter(["age < 50", '"Lactate__max" > 2']).encounter_ids == ["a"]
    assert tiny.filter(pl.col("sex") == "F").encounter_ids == ["b"]
    assert tiny.filter_by_labels(["Tachy"]).encounter_ids == ["b"]
    with pytest.raises(KeyError):
        tiny.filter_by_labels(["Nope"])


def test_bad_filter_raises_filter_error(tiny: Cohort) -> None:
    with pytest.raises(FilterError):
        tiny.filter("no_such_column > 1")
    with pytest.raises(FilterError, match="not boolean"):
        tiny.filter("age + 1")


def test_tag_matrix(tiny: Cohort) -> None:
    tags = tiny.tag_matrix()
    assert tags.columns == [ENCOUNTER_ID, "Old"]
    assert tags["Old"].to_list() == [False, True]


def test_select_keeps_config_and_features(tiny: Cohort) -> None:
    tiny.features()
    sub = tiny.select(["a"])
    assert sub.encounter_ids == ["a"]
    assert sub.config is tiny.config
    assert sub.features().height == 1
    assert sub.variables == ["HR", "Lactate"]


def test_cohort_wide_with_static(tiny: Cohort) -> None:
    w = tiny.wide(every="1h", with_static=True)
    assert set(w.columns) == {ENCOUNTER_ID, TIME, "HR", "Lactate", "age", "sex"}
    assert w.filter(pl.col(ENCOUNTER_ID) == "b").height == 2


def test_summary(tiny: Cohort) -> None:
    s = tiny.summary()
    assert s.n_encounters == 2 and s.n_variables == 2 and s.n_observations == 7
    assert s.los_hours_min == 1.0 and s.los_hours_max == 2.0


def test_to_pandas(tiny: Cohort) -> None:
    pd = pytest.importorskip("pandas")
    frame = tiny.to_pandas(every="1h")
    assert isinstance(frame, pd.DataFrame)
    assert frame.index.names == [ENCOUNTER_ID, TIME]
    e_frame = tiny.get("a").to_pandas()
    assert e_frame.index.name == TIME


def test_config_yaml_roundtrip(tmp_path) -> None:
    cfg = CohortConfig(
        name="x",
        filters=[Rule(label="A", where="age > 1")],
        processing=ProcessingConfig(resample_every="5m", fill="forward"),
    )
    cfg.save(tmp_path / "c.yaml")
    back = CohortConfig.load(tmp_path / "c.yaml")
    assert back.name == "x" and back.filters[0].where == "age > 1"
    assert back.processing.resample_every == "5m"
    with pytest.raises(ValueError):
        Rule(label="empty", where="  ")

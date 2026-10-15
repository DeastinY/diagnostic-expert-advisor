"""Generate a plausible-looking ICU cohort.

Variables are sampled at realistic but different cadences (vitals every minute, blood gases
every four hours, labs twice a day, scores daily), with random gaps and a per-encounter
severity that drives trajectories, outcomes and the Horowitz index. Encounter ids are
non-sequential strings on purpose so that nothing can rely on row order.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

from dea.core.config import CohortConfig, ProcessingConfig, Rule
from dea.core.model import ENCOUNTER_ID, EVENT_SCHEMA, LONG_SCHEMA, TIME, VALUE, VARIABLE, Cohort


@dataclass(frozen=True)
class VarSpec:
    name: str
    label: str
    unit: str
    cadence_s: int
    base: float  # value at severity 0
    slope: float  # change per unit severity
    noise: float  # white noise sd
    walk: float  # random-walk step sd
    lo: float
    hi: float
    integer: bool = False
    gap_prob: float = 0.05


SPECS: tuple[VarSpec, ...] = (
    VarSpec("HR", "Heart rate", "1/min", 60, 72, 45, 2.0, 0.6, 35, 190, True),
    VarSpec("MAP", "Mean arterial pressure", "mmHg", 60, 88, -30, 3.0, 0.7, 40, 140, True),
    VarSpec("SpO2", "Peripheral oxygen saturation", "%", 60, 97.5, -9, 0.6, 0.15, 75, 100, True),
    VarSpec("RR", "Respiratory rate", "1/min", 60, 14, 14, 1.5, 0.4, 6, 45, True),
    VarSpec("Temp", "Core temperature", "°C", 900, 37.0, 1.6, 0.1, 0.05, 34.5, 41.0),
    VarSpec("FiO2", "Inspired oxygen fraction", "", 900, 0.30, 0.6, 0.02, 0.01, 0.21, 1.0),
    VarSpec("PEEP", "Positive end-expiratory pressure", "cmH2O", 900, 5, 10, 0.5, 0.2, 0, 20, True),
    VarSpec(
        "TidalVolume_per_kg", "Tidal volume per kg IBW", "ml/kg", 900, 6.5, 0.8, 0.4, 0.1, 3, 12
    ),
    VarSpec(
        "Compliance", "Respiratory system compliance", "ml/cmH2O", 900, 60, -35, 3.0, 1.0, 10, 100
    ),
    VarSpec("Horowitz", "Horowitz index (paO2/FiO2)", "mmHg", 14400, 380, -280, 20, 15, 40, 550),
    VarSpec("paCO2", "Arterial CO2 partial pressure", "mmHg", 14400, 40, 16, 2.5, 1.0, 20, 90),
    VarSpec("pH", "Arterial pH", "", 14400, 7.41, -0.15, 0.02, 0.01, 6.9, 7.6),
    VarSpec("Lactate", "Arterial lactate", "mmol/l", 14400, 1.0, 5.0, 0.3, 0.2, 0.3, 15),
    VarSpec("Leukocytes", "Leukocyte count", "G/l", 43200, 8, 9, 1.0, 0.5, 1, 40),
    VarSpec("CRP", "C-reactive protein", "mg/l", 43200, 40, 160, 10, 8, 0, 400),
    VarSpec("PCT", "Procalcitonin", "ng/ml", 43200, 0.3, 8.0, 0.2, 0.2, 0.02, 60),
    VarSpec("Creatinine", "Serum creatinine", "mg/dl", 43200, 0.9, 1.6, 0.1, 0.05, 0.3, 8),
    VarSpec("Bilirubin", "Total bilirubin", "mg/dl", 43200, 0.8, 2.0, 0.1, 0.05, 0.1, 20),
    VarSpec(
        "Norepinephrine",
        "Norepinephrine infusion rate",
        "µg/kg/min",
        3600,
        0.0,
        0.5,
        0.03,
        0.02,
        0,
        2.0,
    ),
    VarSpec("SOFA", "SOFA score", "points", 86400, 2, 12, 1.0, 0.5, 0, 24, True),
)


def _trajectory(rng: np.random.Generator, spec: VarSpec, severity: float, n: int) -> np.ndarray:
    target = spec.base + spec.slope * severity
    walk = np.cumsum(rng.normal(0, spec.walk, n))
    # mean-revert the walk so long stays do not drift to the bounds
    walk -= np.linspace(0, walk[-1], n) * 0.7
    values = target + walk + rng.normal(0, spec.noise, n)
    if spec.name == "Norepinephrine" and severity < 0.35:
        values[:] = 0.0
    values = np.clip(values, spec.lo, spec.hi)
    if spec.integer:
        values = np.round(values)
    return values


def _encounter_long(
    rng: np.random.Generator, eid: str, severity: float, los_s: int
) -> pl.DataFrame:
    frames = []
    fio2_times = fio2_values = None
    for spec in SPECS:
        if spec.name == "paO2":
            continue
        times = np.arange(0, los_s + 1, spec.cadence_s, dtype=np.int64)
        values = _trajectory(rng, spec, severity, len(times))
        if spec.gap_prob and len(times) > 20 and rng.random() < 0.6:
            gap_len = int(len(times) * rng.uniform(0.02, spec.gap_prob * 2))
            start = rng.integers(0, max(1, len(times) - gap_len))
            keep = np.ones(len(times), dtype=bool)
            keep[start : start + gap_len] = False
            times, values = times[keep], values[keep]
        if spec.name == "FiO2":
            fio2_times, fio2_values = times, values
        frames.append(pl.DataFrame({TIME: times, VARIABLE: spec.name, VALUE: values}))
        if spec.name == "Horowitz" and fio2_times is not None:
            idx = np.clip(
                np.searchsorted(fio2_times, times, side="right") - 1, 0, len(fio2_times) - 1
            )
            pao2 = np.clip(values * fio2_values[idx] + rng.normal(0, 3, len(times)), 35, 500)
            frames.append(pl.DataFrame({TIME: times, VARIABLE: "paO2", VALUE: pao2}))
    # a constant device identifier leaking into the time series, to exercise the profiler
    bed = float(rng.integers(1, 40))
    frames.append(
        pl.DataFrame(
            {
                TIME: np.arange(0, los_s + 1, 86400, dtype=np.int64),
                VARIABLE: "Bed_Number",
                VALUE: bed,
            }
        )
    )
    long = pl.concat(frames)
    return long.select(
        pl.lit(eid).alias(ENCOUNTER_ID),
        pl.duration(seconds=pl.col(TIME)).cast(LONG_SCHEMA[TIME]).alias(TIME),
        pl.col(VARIABLE).cast(pl.String()),
        pl.col(VALUE).cast(pl.Float64()),
    )


def _encounter_events(
    rng: np.random.Generator, eid: str, severity: float, los_s: int
) -> pl.DataFrame:
    """String-valued observations: ventilation mode every four hours."""
    times = np.arange(0, los_s + 1, 14400, dtype=np.int64)
    modes = np.array(["PSV", "PCV", "VCV", "none"])
    p = np.array([0.4 - 0.3 * severity, 0.3 + 0.2 * severity, 0.2 + 0.1 * severity, 0.1])
    p = p / p.sum()
    values = rng.choice(modes, size=len(times), p=p)
    return pl.DataFrame(
        {
            ENCOUNTER_ID: eid,
            TIME: pl.Series(times).cast(pl.Int64()),
            VARIABLE: "Ventilation_Mode",
            VALUE: values.astype(str),
        }
    ).with_columns(pl.duration(seconds=pl.col(TIME)).cast(EVENT_SCHEMA[TIME]).alias(TIME))


def default_config(name: str = "synthetic") -> CohortConfig:
    """Config with clinically flavoured filters, tags and plots for the synthetic cohort."""
    return CohortConfig(
        name=name,
        description="Synthetic ICU cohort generated by dea.synth; not real patients.",
        filters=[
            Rule(label="Short stay", where="los_hours < 72", description="Less than three days"),
            Rule(label="Long stay", where="los_hours >= 168", description="A week or longer"),
            Rule(
                label="Severe ARDS",
                where='"Horowitz__min" < 100',
                description="Horowitz index below 100 mmHg at any time",
            ),
            Rule(
                label="Vasopressors",
                where='"Norepinephrine__max" > 0',
                description="Received norepinephrine",
            ),
            Rule(label="Deceased", where="outcome = 'deceased'"),
        ],
        tags=[
            Rule(label="Short stay", where="los_hours < 72"),
            Rule(label="Severe ARDS", where='"Horowitz__min" < 100'),
            Rule(label="Moderate ARDS", where='"Horowitz__min" >= 100 AND "Horowitz__min" < 200'),
            Rule(label="Mild ARDS", where='"Horowitz__min" >= 200 AND "Horowitz__min" < 300'),
            Rule(label="Vasopressors", where='"Norepinephrine__max" > 0'),
        ],
        plots=["HR", "MAP", "SpO2", "Horowitz", "FiO2", "PEEP", "Lactate", "Norepinephrine"],
        variable_labels={s.name: s.label for s in SPECS}
        | {"paO2": "Arterial O2 partial pressure", "Bed_Number": "Bed number"},
        variable_units={s.name: s.unit for s in SPECS if s.unit} | {"paO2": "mmHg"},
        processing=ProcessingConfig(
            resample_every="15m", aggregate="mean", fill="forward", drop_variables=["Bed_Number"]
        ),
        source="dea.synth.generate_cohort",
    )


def _ids(rng: np.random.Generator, n: int) -> list[str]:
    ids: set[str] = set()
    while len(ids) < n:
        ids.add(f"ICU-{rng.integers(2019, 2025)}-{rng.integers(10_000, 99_999)}")
    return sorted(ids, key=lambda _: rng.random())


def _generate_batch(
    rng: np.random.Generator, ids: list[str], min_hours: float, max_hours: float
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Static table, numeric observations and events for the given encounter ids."""
    static_rows, dynamics, events = [], [], []
    for eid in ids:
        severity = float(np.clip(rng.beta(2, 3), 0.02, 0.98))
        los_h = float(
            np.clip(
                math.exp(rng.normal(math.log(72), 0.8)) * (0.7 + severity), min_hours, max_hours
            )
        )
        los_s = int(los_h * 3600)
        sex = "F" if rng.random() < 0.42 else "M"
        admission = rng.choice(["medical", "surgical", "trauma"], p=[0.55, 0.35, 0.10])
        deceased = rng.random() < 0.08 + 0.45 * severity**2
        static_rows.append(
            {
                ENCOUNTER_ID: eid,
                "age": int(np.clip(rng.normal(63, 15), 18, 96)),
                "sex": sex,
                "height_cm": float(np.round(rng.normal(165 if sex == "F" else 178, 7))),
                "weight_kg": float(np.round(rng.normal(72 if sex == "F" else 85, 14))),
                "admission_type": str(admission),
                "admission_year": int(eid.split("-")[1]),
                "outcome": "deceased" if deceased else "survived",
            }
        )
        dynamics.append(_encounter_long(rng, eid, severity, los_s))
        events.append(_encounter_events(rng, eid, severity, los_s))
    return pl.DataFrame(static_rows).sort(ENCOUNTER_ID), pl.concat(dynamics), pl.concat(events)


def generate_cohort(
    n_encounters: int = 40,
    seed: int = 0,
    name: str = "synthetic",
    min_hours: float = 12,
    max_hours: float = 21 * 24,
) -> Cohort:
    """Generate ``n_encounters`` synthetic encounters in memory."""
    rng = np.random.default_rng(seed)
    static, dynamic, events = _generate_batch(rng, _ids(rng, n_encounters), min_hours, max_hours)
    return Cohort(static, dynamic, events=events, config=default_config(name))


def write_synthetic(
    path: Path | str,
    n_encounters: int,
    seed: int = 0,
    name: str | None = None,
    batch_size: int = 200,
    min_hours: float = 12,
    max_hours: float = 21 * 24,
) -> Path:
    """Generate a cohort of any size straight to disk, ``batch_size`` encounters at a time."""
    from dea.io.storage import write_cohort_batches

    rng = np.random.default_rng(seed)
    ids = _ids(rng, n_encounters)
    batches = (
        _generate_batch(rng, ids[i : i + batch_size], min_hours, max_hours)
        for i in range(0, len(ids), batch_size)
    )
    config = default_config(name or Path(path).name)
    return write_cohort_batches(path, config, batches, n_encounters)


def write_legacy_csv_tree(cohort: Cohort, path: Path | str, every: str = "1m") -> Path:
    """Write ``cohort`` in the DEA v1 CSV layout (for importer tests and migration demos)."""
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    static = cohort.static.to_pandas().set_index(ENCOUNTER_ID) if False else cohort.static
    # legacy layout: unnamed first column is the id
    static.rename({ENCOUNTER_ID: ""}).write_csv(root / "static.csv")
    for enc in cohort:
        wide = enc.wide(every=every, fill="forward")
        minutes = (wide.get_column(TIME).dt.total_seconds() // 60).cast(pl.Int64())
        out = wide.drop(TIME).with_columns(minutes.alias("")).select("", pl.exclude(""))
        (root / enc.id).mkdir(exist_ok=True)
        out.write_csv(root / enc.id / "dynamic.csv")
    return root
